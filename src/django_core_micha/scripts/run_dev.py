# webapp-management/src/django_core_micha/scripts/run_dev.py
import argparse
import builtins
import functools
import subprocess
import sys
import shutil
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

# Import existing scripts as modules
from django_core_micha.scripts import generate_env
from django_core_micha.scripts.drift_check import collect_drift_warnings

# DCM-DX-5: unbuffered output -- a long `--build`/`--watch` run must not look like a
# stall to an agent following AGENTS.md's ">5 min silence = suspected stall" rule.
print = functools.partial(builtins.print, flush=True)

BACKEND_READINESS_TIMEOUT_SECONDS = 60
BACKEND_READINESS_POLL_INTERVAL_SECONDS = 1.0
FRONTEND_WATCH_HEARTBEAT_SECONDS = 60


# Optional local-dev-only services, gated behind a same-named Compose profile in
# docker-compose.local.yml (never in the base/prod compose file). Celery's need is
# auto-detected from whether the project's compose files define it at all; java has
# no auto-detection (most consumers never need it) and stays an explicit opt-in.
OPTIONAL_LOCAL_SERVICE_PROFILES = {
    "celery": ["celery_worker", "celery_beat"],
    "java": ["java_backend"],
}


def normalize_project_name(name: str) -> str:
    normalized = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in name.lower())
    normalized = normalized.strip("-_")
    return normalized or "spool"

def run_command(command, cwd=None, ignore_errors=False, shell=False, capture_output=False):
    """Helper function to execute shell commands."""
    if not capture_output:
        print(f"[INFO] Running: {' '.join(command) if isinstance(command, list) else command}")
    
    try:
        is_windows = sys.platform == "win32"
        use_shell = shell or is_windows
        
        if capture_output:
            return subprocess.Popen(command, cwd=cwd, shell=use_shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        subprocess.run(command, check=True, cwd=cwd, shell=use_shell)
    except subprocess.CalledProcessError as e:
        if ignore_errors:
            print(f"[WARN] Command failed (ignored): {e}")
        else:
            print(f"[ERROR] Command failed: {e}")
            sys.exit(e.returncode)

def load_compose_service_names(compose_files_args):
    """Best-effort parse of the compose files passed via `-f` flags to discover which
    services they define (merged across base + overlays), so run-dev can auto-detect
    optional local-dev services (e.g. celery) without requiring a manual flag."""
    names = set()
    it = iter(compose_files_args)
    for token in it:
        if token != "-f":
            continue
        try:
            file_path = next(it)
        except StopIteration:
            break
        path = Path(file_path)
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            continue
        names.update((data.get("services") or {}).keys())
    return names


def resolve_build_and_uv_flags(build, refresh_deps, refresh):
    """`--build` alone matches staging's default `ARG UV_FLAGS=""` (hits the Dockerfile's
    uv cache mount in full); only `--refresh-deps` sets UV_FLAGS=--refresh and it implies
    --build. The deprecated `--refresh` keeps meaning exactly --build, not a refresh."""
    should_build = bool(build or refresh_deps or refresh)
    uv_flags = "--refresh" if refresh_deps else ""
    return should_build, uv_flags


def compute_active_local_profiles(*, celery_flag, java_flag, local_profiles_eligible, compose_service_names):
    """Decide which OPTIONAL_LOCAL_SERVICE_PROFILES entries are active for this run.

    Celery is auto-detected (active whenever the project's own compose files already
    define a `celery_worker` service, or `--celery` forces it); java has no
    auto-detection and is active only via the explicit `--java` flag. Neither applies
    outside local dev (edge/spool run every optional service unconditionally, with no
    profile gate at all, so this function is never consulted there).
    """
    if not local_profiles_eligible:
        return {"celery": False, "java": False}
    return {
        "celery": bool(celery_flag or "celery_worker" in compose_service_names),
        "java": bool(java_flag),
    }


def compute_expected_compose_project_name(explicit_override, project_name_from_yaml, env_mode, fallback_name):
    """Mirror generate_env.py's own COMPOSE_PROJECT_NAME formula
    (`f"{project_name}_{env_name}"`, written into `.env` and auto-loaded by
    docker-compose) instead of guessing from the directory name -- an explicit
    `--compose-project-name`/`--spool` override always wins; `project.yaml` is read
    directly (never `.env`, which this codebase must not read)."""
    if explicit_override:
        return explicit_override
    if project_name_from_yaml:
        return f"{project_name_from_yaml}_{env_mode}"
    return fallback_name


def load_project_yaml_name(base_dir, config_path="project.yaml"):
    path = base_dir / config_path
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None
    return data.get("project_name")


def should_remove_traefik_container(container_project, own_project_name):
    """A local traefik container is removed only when it belongs to THIS invocation's
    own Compose project -- a foreign one (in practice webapp-management's) is left
    alone. No container at all is handled by the caller before this is even consulted."""
    if not container_project:
        return False
    return container_project == own_project_name


def get_traefik_container_project():
    """Best-effort read of the local traefik container's own Compose project label.
    Returns None when no such container exists (nothing to remove, nothing to keep)."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", '{{ index .Config.Labels "com.docker.compose.project" }}', "traefik"],
            capture_output=True,
            text=True,
            check=True,
            shell=(sys.platform == "win32"),
        )
    except subprocess.CalledProcessError:
        return None
    project = result.stdout.strip()
    return project or None


def parse_compose_port_output(output):
    """Parse `docker-compose ... port backend 8000` output (e.g. '0.0.0.0:32771')."""
    first_line = (output or "").strip().splitlines()[0].strip() if (output or "").strip() else ""
    if ":" not in first_line:
        return None
    port = first_line.rsplit(":", 1)[-1]
    return port if port.isdigit() else None


def resolve_backend_port(compose_files_args):
    """Resolve the backend's published port via Compose itself -- never by reading
    `.env` (AGENTS.md forbids reading it; WEB_PORT lives there)."""
    try:
        result = subprocess.run(
            ["docker-compose"] + compose_files_args + ["port", "backend", "8000"],
            capture_output=True,
            text=True,
            check=True,
            shell=(sys.platform == "win32"),
        )
    except subprocess.CalledProcessError:
        return None
    return parse_compose_port_output(result.stdout)


def check_http_ready(url, timeout_seconds=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def format_ready_line(url):
    return f"READY {url}"


def format_timeout_line(seconds):
    return f"TIMEOUT after {seconds}s"


def wait_for_backend_ready(is_ready, url, timeout_seconds, poll_interval_seconds,
                            sleep_fn=time.sleep, now_fn=time.monotonic):
    """Poll `is_ready()` until it returns True or `timeout_seconds` elapses, using
    injectable clock/sleep so this is testable without real waiting or Docker."""
    deadline = now_fn() + timeout_seconds
    while now_fn() < deadline:
        if is_ready():
            return format_ready_line(url)
        sleep_fn(poll_interval_seconds)
    return format_timeout_line(timeout_seconds)


def announce_backend_readiness(compose_files_args):
    """The `--no-log-stream` readiness gate: gives agent sessions the same
    parse-one-line contract AGENTS.md already relies on for Codex (RESULT: DONE|BLOCKED)."""
    port = resolve_backend_port(compose_files_args)
    if port is None:
        print("[WARN] Could not resolve the backend port via 'docker-compose ... port backend 8000'; skipping readiness wait.")
        return
    url = f"http://localhost:{port}/"
    print(f"[INFO] Waiting for backend to become ready at {url} (timeout {BACKEND_READINESS_TIMEOUT_SECONDS}s)...")
    print(wait_for_backend_ready(
        lambda: check_http_ready(url),
        url,
        BACKEND_READINESS_TIMEOUT_SECONDS,
        BACKEND_READINESS_POLL_INTERVAL_SECONDS,
    ))


def stream_docker_logs(compose_files_args, services):
    """
    Runs in a separate thread to stream docker logs while frontend is running.
    """
    print("[INFO] Starting Docker Log Stream...")
    cmd = ["docker-compose"] + compose_files_args + ["logs", "-f", "--tail=10"] + services
    
    try:
        # Explicitly piping stdout/stderr to the current process's streams
        subprocess.run(
            cmd, 
            check=False, 
            shell=(sys.platform == "win32"),
            stdout=sys.stdout,
            stderr=sys.stderr
        )
    except Exception as e:
        print(f"[WARN] Log stream interrupted: {e}")


def frontend_needs_pnpm_install(node_modules_exists, marker_mtime, source_mtimes):
    """Decide whether `pnpm install` must run: missing node_modules (existing
    behaviour, unbroken), no install-state marker yet, or package.json/pnpm-lock.yaml
    newer than pnpm's own marker (`node_modules/.modules.yaml`)."""
    if not node_modules_exists:
        return True
    if marker_mtime is None:
        return True
    return any(source_mtime > marker_mtime for source_mtime in source_mtimes)


def ensure_frontend_node_modules(frontend_dir):
    node_modules = frontend_dir / "node_modules"
    marker = node_modules / ".modules.yaml"
    source_paths = [frontend_dir / "package.json", frontend_dir / "pnpm-lock.yaml"]

    marker_mtime = marker.stat().st_mtime if marker.exists() else None
    source_mtimes = [path.stat().st_mtime for path in source_paths if path.exists()]

    if not frontend_needs_pnpm_install(node_modules.exists(), marker_mtime, source_mtimes):
        return

    if not node_modules.exists():
        print("[INFO] node_modules not found. Running pnpm install...")
    else:
        print("[INFO] package.json/pnpm-lock.yaml changed since the last install. Running pnpm install...")
    subprocess.run("pnpm install", cwd=str(frontend_dir), shell=True, check=True)


def frontend_cli_executable(name):
    if sys.platform == "win32":
        return f"{name}.cmd"
    return name


def run_host_frontend_process(frontend_dir, compose_files_args, log_services, command, label):
    """Run a host-side frontend process while streaming relevant docker logs."""
    log_thread = threading.Thread(
        target=stream_docker_logs,
        args=(compose_files_args, log_services),
        daemon=True,
    )
    log_thread.start()

    try:
        ensure_frontend_node_modules(frontend_dir)
        print(f"[INFO] Executing '{command}' in {frontend_dir}...")
        subprocess.run(command, cwd=str(frontend_dir), shell=True, check=True)
    except KeyboardInterrupt:
        print(f"\n[INFO] Stopping {label}...")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {label} process failed: {e}")


FRONTEND_ACTIVE_BUILD_DIR = "build_current"
FRONTEND_NEXT_BUILD_DIR = "build_next"
FRONTEND_PREV_BUILD_DIR = "build_prev"
FRONTEND_LOCAL_HOST_BUILD_MODE = "local-host-build"
FRONTEND_WATCH_POLL_SECONDS = 1.0
FRONTEND_WATCH_DEBOUNCE_SECONDS = 0.75


def atomic_frontend_build_paths(frontend_dir):
    return {
        "active": frontend_dir / FRONTEND_ACTIVE_BUILD_DIR,
        "next": frontend_dir / FRONTEND_NEXT_BUILD_DIR,
        "previous": frontend_dir / FRONTEND_PREV_BUILD_DIR,
    }


def cleanup_path(path):
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def sync_priority(entry):
    if entry.is_dir():
        if entry.name == "static":
            return (0, entry.name)
        return (10, entry.name)

    if entry.name in {"index.js", "index.css"}:
        return (90, entry.name)
    if entry.name == "index.html":
        return (100, entry.name)
    return (20, entry.name)


def sync_tree(source, destination):
    destination.mkdir(parents=True, exist_ok=True)

    source_entries = {
        entry.name: entry
        for entry in sorted(source.iterdir(), key=sync_priority)
    }
    destination_entries = {entry.name: entry for entry in destination.iterdir()}

    for name, source_entry in source_entries.items():
        destination_entry = destination / name
        if source_entry.is_dir():
            if destination_entry.exists() and not destination_entry.is_dir():
                destination_entry.unlink()
            sync_tree(source_entry, destination_entry)
        else:
            destination_entry.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_entry, destination_entry)

    for name, destination_entry in destination_entries.items():
        if name in source_entries:
            continue
        cleanup_path(destination_entry)


def run_host_frontend_build_once(frontend_dir):
    ensure_frontend_node_modules(frontend_dir)
    build_paths = atomic_frontend_build_paths(frontend_dir)

    cleanup_path(build_paths["next"])

    print(f"[INFO] Executing atomic frontend build into '{build_paths['next'].name}'...")
    frontend_env = os.environ.copy()
    frontend_env["VITE_LOCAL_HOST_BUILD"] = "1"
    subprocess.run(
        [
            frontend_cli_executable("pnpm"),
            "exec",
            "vite",
            "build",
            "--mode",
            FRONTEND_LOCAL_HOST_BUILD_MODE,
            "--outDir",
            build_paths["next"].name,
        ],
        cwd=str(frontend_dir),
        check=True,
        env=frontend_env,
    )

    next_index = build_paths["next"] / "index.html"
    next_static = build_paths["next"] / "static"
    if not next_index.is_file() or not next_static.is_dir():
        raise RuntimeError("Atomic frontend build did not produce index.html and static/ in build_next.")

    print(f"[INFO] Syncing '{build_paths['next'].name}' into stable '{build_paths['active'].name}'...")
    sync_tree(build_paths["next"], build_paths["active"])
    cleanup_path(build_paths["next"])


def local_compose_uses_host_frontend_build(base_dir):
    compose_path = base_dir / "docker-compose.local.yml"
    if not compose_path.exists():
        return False
    try:
        content = compose_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "FRONTEND_BUILD_DIR=" in content


def iter_frontend_watch_files(frontend_dir):
    ignored_dirs = {
        "node_modules",
        ".git",
        FRONTEND_ACTIVE_BUILD_DIR,
        FRONTEND_NEXT_BUILD_DIR,
        FRONTEND_PREV_BUILD_DIR,
        "build",
        "dist",
        ".vite",
    }
    for path in frontend_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_dirs for part in path.parts):
            continue
        yield path


def snapshot_frontend_files(frontend_dir):
    snapshot = {}
    for path in iter_frontend_watch_files(frontend_dir):
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue
    return snapshot


def run_host_frontend_watch_loop(frontend_dir, compose_files_args, log_services):
    log_thread = threading.Thread(
        target=stream_docker_logs,
        args=(compose_files_args, log_services),
        daemon=True,
    )
    log_thread.start()

    previous_snapshot = snapshot_frontend_files(frontend_dir)
    print(f"[INFO] Watching frontend sources in {frontend_dir}...")
    seconds_since_heartbeat = 0.0

    try:
        while True:
            time.sleep(FRONTEND_WATCH_POLL_SECONDS)
            seconds_since_heartbeat += FRONTEND_WATCH_POLL_SECONDS
            current_snapshot = snapshot_frontend_files(frontend_dir)
            if current_snapshot == previous_snapshot:
                if seconds_since_heartbeat >= FRONTEND_WATCH_HEARTBEAT_SECONDS:
                    print(f"[INFO] Still watching {frontend_dir} (no change in the last {FRONTEND_WATCH_HEARTBEAT_SECONDS}s)...")
                    seconds_since_heartbeat = 0.0
                continue
            seconds_since_heartbeat = 0.0
            previous_snapshot = current_snapshot
            time.sleep(FRONTEND_WATCH_DEBOUNCE_SECONDS)
            previous_snapshot = snapshot_frontend_files(frontend_dir)
            print("[INFO] Frontend source change detected. Rebuilding atomically...")
            run_host_frontend_build_once(frontend_dir)
    except KeyboardInterrupt:
        print("\n[INFO] Stopping frontend build loop...")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Frontend build loop failed: {e}")
    except RuntimeError as e:
        print(f"[ERROR] Frontend build loop failed: {e}")


def run_backend_maintenance(compose_files_args, args):
    """Run optional Django maintenance commands inside the backend container."""
    if not (args.migrate or args.makemigrations or args.translate):
        return

    base_cmd = ["docker-compose"] + compose_files_args + ["exec", "-T", "backend", "python", "manage.py"]
    maintenance_steps = []

    if args.makemigrations:
        maintenance_steps.append(("[INFO] Running Django makemigrations...", ["makemigrations"]))

    if args.migrate:
        maintenance_steps.append(("[INFO] Running Django migrate...", ["migrate"]))

    if args.translate:
        maintenance_steps.append(("[INFO] Updating translation files (.po)...", ["makemessages", "-a"]))
        maintenance_steps.append(("[INFO] Compiling translation files (.mo)...", ["compilemessages"]))

    for message, command_suffix in maintenance_steps:
        print(message)
        run_command(base_cmd + command_suffix)


def cleanup_optional_local_services(compose_files_args, args, active_profiles):
    """Remove optional local-only services that are not part of THIS run, so a
    service started by an earlier invocation with different flags (e.g. --celery
    or --java on a previous run) doesn't silently keep running."""
    if args.spool or args.edge or not Path("docker-compose.local.yml").exists():
        return

    stale_services = [
        service
        for profile, services in OPTIONAL_LOCAL_SERVICE_PROFILES.items()
        if profile not in active_profiles
        for service in services
    ]
    if not stale_services:
        return

    previous_profiles = os.environ.get("COMPOSE_PROFILES")
    try:
        # Enable every optional profile temporarily so compose can target and remove them.
        os.environ["COMPOSE_PROFILES"] = ",".join(OPTIONAL_LOCAL_SERVICE_PROFILES.keys())
        for service in stale_services:
            print(f"[INFO] Removing stale local {service} container...")
            run_command(
                ["docker-compose"] + compose_files_args + ["rm", "-f", "-s", service],
                ignore_errors=True,
            )
    finally:
        if previous_profiles is None:
            os.environ.pop("COMPOSE_PROFILES", None)
        else:
            os.environ["COMPOSE_PROFILES"] = previous_profiles

def main():
    parser = argparse.ArgumentParser(description="Developer Runner for Docker setup")
    parser.add_argument("--edge", action="store_true", help="Set environment to edge")
    parser.add_argument("--env", action="store_true", help="Generate local .env file")
    parser.add_argument("--vite", action="store_true", help="Use Hot-Reloading Mode (Vite on Host)")
    parser.add_argument(
        "--build",
        action="store_true",
        help=(
            "Build Docker images before starting the stack. Uses the Dockerfile's "
            "default uv cache (fast, matches staging); pass --refresh-deps as well "
            "to force a dependency refresh."
        ),
    )
    parser.add_argument(
        "--refresh-deps",
        action="store_true",
        help=(
            "Force a uv dependency refresh during the build (bypasses the uv cache "
            "mount) -- for when a dependency's contents changed without its pin "
            "changing. Implies --build."
        ),
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Run a full 'vite build' host-side after every frontend source change -- "
            "not HMR (use --vite for Hot Module Reloading). Slower per change than "
            "--vite, but serves through the same atomic production-build path as --build."
        ),
    )
    parser.add_argument(
        "--celery",
        action="store_true",
        help=(
            "Force-enable the optional celery_worker/celery_beat in local development. "
            "Usually unnecessary: run-dev auto-enables them when the project's compose "
            "files already define a celery_worker service."
        ),
    )
    parser.add_argument(
        "--java",
        action="store_true",
        help=(
            "Start the optional java_backend in local development. Off by default -- "
            "most projects no longer need it; pass this only when a task genuinely "
            "requires the Java backend (e.g. a legacy-engine run in hram)."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--migrate",
        "--m",
        dest="migrate",
        action="store_true",
        help="Run Django migrate after the backend is up",
    )
    parser.add_argument(
        "--makemigrations",
        "--mm",
        dest="makemigrations",
        action="store_true",
        help="Run Django makemigrations after the backend is up",
    )
    parser.add_argument(
        "--translate",
        "--t",
        dest="translate",
        action="store_true",
        help="Run makemessages and compilemessages after the backend is up",
    )
    parser.add_argument(
        "--no-log-stream",
        action="store_true",
        help="Start the configured services and exit without attaching to logs or host frontend processes",
    )
    parser.add_argument(
        "--compose-file",
        dest="compose_files",
        action="append",
        default=[],
        help="Add an extra compose file after the default mode-specific compose files.",
    )
    parser.add_argument(
        "--compose-project-name",
        help="Override COMPOSE_PROJECT_NAME for this run.",
    )
    parser.add_argument(
        "--spool",
        action="store_true",
        help="Run in isolated spool mode with its own compose project name and docker-compose.spool.yml.",
    )

    args = parser.parse_args()

    # --- KONFIGURATION (FIX) ---
    # Wir nutzen das aktuelle Arbeitsverzeichnis (wo du den Befehl ausführst),
    # nicht den Speicherort des Skripts.
    BASE_DIR = Path.cwd()
    frontend_dir = BASE_DIR / "frontend"
    
    env_mode = "edge" if args.edge else "local"
    MODE = "VITE" if args.vite else "CLASSIC"
    FORCE_ENV = args.env
    uses_host_frontend_build = frontend_dir.exists() and local_compose_uses_host_frontend_build(BASE_DIR)

    if args.vite and args.watch:
        print("[ERROR] --watch cannot be combined with --vite.")
        sys.exit(1)

    if args.watch and args.no_log_stream:
        print(
            "[INFO] --watch + --no-log-stream: performing one atomic host frontend "
            "build, then leaving containers running in detached mode -- the "
            "continuous watch loop does not start (nothing to watch for once detached)."
        )

    if args.refresh and not args.build:
        print("[WARN] --refresh is deprecated; use --build instead.")

    should_build, uv_flags_value = resolve_build_and_uv_flags(args.build, args.refresh_deps, args.refresh)

    if args.compose_project_name:
        os.environ["COMPOSE_PROJECT_NAME"] = args.compose_project_name

    if args.spool:
        project_name = args.compose_project_name or f"{normalize_project_name(BASE_DIR.name)}_spool"
        os.environ["COMPOSE_PROJECT_NAME"] = project_name
        os.environ["CONTAINER_NAME_PREFIX"] = project_name
        os.environ["TRAEFIK_ENABLE"] = "false"
        os.environ["USE_EXTERNAL_PROXY"] = "false"
        os.environ["BACKUP_ENABLE"] = "false"
        os.environ["DB_VOLUME_NAME"] = f"{project_name}_postgres_data"
        os.environ["MEDIA_VOLUME_NAME"] = f"{project_name}_media_volume"
        os.environ["EXCEL_VOLUME_NAME"] = f"{project_name}_excel_volume"

    os.environ["UV_FLAGS"] = uv_flags_value
    if should_build:
        if uv_flags_value:
            print("[INFO] Docker build ACTIVE: image rebuild will also force-refresh Python dependencies (--refresh-deps).")
        else:
            print("[INFO] Docker build ACTIVE: using the Dockerfile's default uv cache (pass --refresh-deps to force a refresh).")

    # DEBUG OUTPUT: Damit wir sehen, wo er sucht
    print(f"[DEBUG] Searching for frontend in: {frontend_dir}")

    # DX-4: warn (never block) when .env has outlived project.yaml, or the local
    # venv has outlived requirements.txt. Silent on a clean start.
    for warning in collect_drift_warnings(BASE_DIR, env_mode):
        print(f"[WARN] {warning}")

    # --- SCHRITT 1: ENV GENERATION ---
    if FORCE_ENV:
        print(f"[INFO] Generating .env for {env_mode}...")
        sys.argv = ["generate-env", "--env", env_mode]
        generate_env.main()

        frontend_env_path = frontend_dir / ".env"
        if frontend_dir.exists():
            shutil.copy(".env", frontend_env_path)
    else:
        print("[INFO] Skipping .env generation (use --env to force; for pushing to GitHub use `sync-secrets --server`)")


    # --- SCHRITT 2: DOCKER FILES ---
    compose_files_args = ["-f", "docker-compose.yml"]
    if args.edge:
        if Path("docker-compose.edge.yml").exists():
            compose_files_args.extend(["-f", "docker-compose.edge.yml"])
    elif MODE == "VITE":
        if Path("docker-compose.local.yml").exists():
            compose_files_args.extend(["-f", "docker-compose.local.yml"])
        else:
            print("[ERROR] --vite requires docker-compose.local.yml!")
            sys.exit(1)
    elif MODE == "CLASSIC":
        # New default: prefer local compose even in CLASSIC mode.
        # This keeps local mount protections (templates/static) consistent.
        if args.spool:
            if Path("docker-compose.spool.yml").exists():
                compose_files_args.extend(["-f", "docker-compose.spool.yml"])
            else:
                print("[ERROR] --spool requires docker-compose.spool.yml!")
                sys.exit(1)
        elif Path("docker-compose.local.yml").exists():
            compose_files_args.extend(["-f", "docker-compose.local.yml"])
        elif Path("docker-compose.override.yml").exists():
            print("[WARN] docker-compose.local.yml not found, falling back to docker-compose.override.yml")
            compose_files_args.extend(["-f", "docker-compose.override.yml"])

    for compose_file in args.compose_files:
        if not Path(compose_file).exists():
            print(f"[ERROR] compose file not found: {compose_file}")
            sys.exit(1)
        compose_files_args.extend(["-f", compose_file])

    print(f"[INFO] Compose files: {' '.join(compose_files_args)}")
    print(f"[INFO] Compose project: {os.environ.get('COMPOSE_PROJECT_NAME', '(default)')}")

    # Optional local-dev services (celery, java): gated behind a Compose profile that
    # only exists in docker-compose.local.yml, so this never applies to edge/spool.
    local_profiles_eligible = not args.edge and not args.spool and Path("docker-compose.local.yml").exists()
    compose_service_names = load_compose_service_names(compose_files_args) if local_profiles_eligible else set()
    profile_state = compute_active_local_profiles(
        celery_flag=args.celery,
        java_flag=args.java,
        local_profiles_eligible=local_profiles_eligible,
        compose_service_names=compose_service_names,
    )
    celery_active = profile_state["celery"]
    java_active = profile_state["java"]

    active_profiles = [profile for profile, active in profile_state.items() if active]

    if active_profiles:
        os.environ["COMPOSE_PROFILES"] = ",".join(active_profiles)
        print(f"[INFO] Local profiles enabled: {', '.join(active_profiles)}")
    else:
        os.environ.pop("COMPOSE_PROFILES", None)

    print(f"==================================================")
    print(
        f"[INFO] RUN-DEV | Mode: {MODE} | Env-Regen: {FORCE_ENV} | "
        f"Celery: {celery_active} | Java: {java_active} | Build: {should_build} | Watch: {args.watch}"
    )
    print(f"==================================================")

    log_services = ["backend"]
    if celery_active:
        log_services.append("celery_worker")

    # --- SCHRITT 3: CLEANUP ---
    if args.spool:
        print("[INFO] Spool mode active, skipping local cleanup steps.")
    else:
        print("[INFO] Stopping containers...")
        own_project_name = compute_expected_compose_project_name(
            os.environ.get("COMPOSE_PROJECT_NAME"),
            load_project_yaml_name(BASE_DIR),
            env_mode,
            normalize_project_name(BASE_DIR.name),
        )
        traefik_project = get_traefik_container_project()
        if traefik_project is None:
            pass  # no local traefik container -- nothing to remove
        elif should_remove_traefik_container(traefik_project, own_project_name):
            print("[INFO] Removing this project's traefik container...")
            run_command(["docker", "rm", "-f", "traefik"], ignore_errors=True)
        else:
            print(f"[INFO] Leaving foreign traefik container (project: {traefik_project}) untouched.")
        cleanup_optional_local_services(compose_files_args, args, active_profiles)


    # --- SCHRITT 4: START ---
    # Always renew anonymous volumes to avoid stale frontend static/templates artifacts.
    up_flags = ["-d", "--remove-orphans", "--renew-anon-volumes"]
    
    if MODE == "CLASSIC":
        if should_build:
            print("[INFO] Starting Classic Docker Build...")
            run_command(["docker-compose"] + compose_files_args + ["build"])
        else:
            print("[INFO] Skipping Docker build (use --build to rebuild images).")
        
        print("[INFO] Starting Containers (Detached)...")
        # 1. Alles im Hintergrund starten (damit Java uns nicht vollquatscht)
        if (should_build or args.watch) and not args.spool and uses_host_frontend_build:
            print("[INFO] Local compose uses host frontend build artifacts. Preparing atomic host frontend build...")
            run_host_frontend_build_once(frontend_dir)
        run_command(["docker-compose"] + compose_files_args + ["up"] + up_flags)
        run_backend_maintenance(compose_files_args, args)

        if args.no_log_stream:
            print("[INFO] --no-log-stream active, leaving containers running in detached mode.")
            announce_backend_readiness(compose_files_args)
            return

        if args.watch:
            if frontend_dir.exists():
                print("[INFO] Starting frontend build loop...")
                try:
                    run_host_frontend_watch_loop(
                        frontend_dir,
                        compose_files_args,
                        log_services,
                    )
                finally:
                    print("\n[INFO] Stopping containers...")
                    run_command(["docker-compose"] + compose_files_args + ["stop"])
            else:
                print(f"[WARN] No frontend directory found at {frontend_dir}!")
                print(f"[INFO] Streaming logs for {' & '.join(log_services)} (Ctrl+C to stop)...")
                try:
                    cmd = ["docker-compose"] + compose_files_args + ["logs", "-f"] + log_services
                    subprocess.run(cmd, check=True, shell=(sys.platform=="win32"))
                except KeyboardInterrupt:
                    print("\n[INFO] Stopping containers...")
                    run_command(["docker-compose"] + compose_files_args + ["stop"])
            return
        
        print(f"[INFO] Streaming logs for {' & '.join(log_services)} (Ctrl+C to stop)...")
        try:
            # 2. Nur RELEVANTE Logs anzeigen (Main Thread blockiert hier)
            cmd = ["docker-compose"] + compose_files_args + ["logs", "-f"] + log_services
            subprocess.run(cmd, check=True, shell=(sys.platform=="win32"))
        except KeyboardInterrupt:
            print("\n[INFO] Stopping containers...")
            # 3. Aufräumen: Wenn du CTRL+C drückst, stoppen wir alles (wie beim normalen 'up')
            run_command(["docker-compose"] + compose_files_args + ["stop"])

    elif MODE == "VITE":
        # 1. Backend starten
        if should_build:
            print("[INFO] Starting Docker Build for Vite mode...")
            run_command(["docker-compose"] + compose_files_args + ["build"])
        else:
            print("[INFO] Skipping Docker build for Vite mode (use --build to rebuild images).")
        print("[INFO] Starting Backend Containers...")
        run_command(["docker-compose"] + compose_files_args + ["up"] + up_flags)
        run_backend_maintenance(compose_files_args, args)

        if args.no_log_stream:
            print("[INFO] --no-log-stream active, leaving backend containers running in detached mode.")
            announce_backend_readiness(compose_files_args)
            return
        
        # 2. Frontend
        if frontend_dir.exists():
            print("\n[INFO] Starting Vite...")
            try:
                run_host_frontend_process(
                    frontend_dir,
                    compose_files_args,
                    log_services,
                    "pnpm dev",
                    "Frontend",
                )
            except KeyboardInterrupt:
                print("\n[INFO] Stopping...")
        else:
             print(f"[WARN] No frontend directory found at {frontend_dir}!")
             run_command(["docker-compose"] + compose_files_args + ["logs", "-f"] + log_services)

if __name__ == "__main__":
    main()
