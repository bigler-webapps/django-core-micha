# webapp-management/src/django_core_micha/scripts/run_dev.py
import argparse
import subprocess
import sys
import shutil
import os
import threading
import time
from pathlib import Path

# Import existing scripts as modules
from django_core_micha.scripts import generate_env, sync_secrets


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


def ensure_frontend_node_modules(frontend_dir):
    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        print("[INFO] node_modules not found. Running pnpm install...")
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

    try:
        while True:
            time.sleep(FRONTEND_WATCH_POLL_SECONDS)
            current_snapshot = snapshot_frontend_files(frontend_dir)
            if current_snapshot == previous_snapshot:
                continue
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


def cleanup_optional_local_services(compose_files_args, args):
    """Remove optional local-only services that should not survive across runs."""
    if args.spool or args.edge or args.celery or not Path("docker-compose.local.yml").exists():
        return

    previous_profiles = os.environ.get("COMPOSE_PROFILES")
    try:
        # Enable the optional service temporarily so compose can target and remove it.
        os.environ["COMPOSE_PROFILES"] = "celery"
        print("[INFO] Removing stale local celery_worker container...")
        run_command(
            ["docker-compose"] + compose_files_args + ["rm", "-f", "-s", "celery_worker"],
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
    env_group = parser.add_mutually_exclusive_group()
    env_group.add_argument("--env", action="store_true", help="Generate local .env file")
    env_group.add_argument(
        "--env-all",
        "--all",
        dest="env_all",
        action="store_true",
        help="Generate local .env and sync GitHub secrets (--all is kept as legacy alias)",
    )
    parser.add_argument("--vite", action="store_true", help="Use Hot-Reloading Mode (Vite on Host)")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build Docker images before starting the stack (also refreshes Python dependencies).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run a host-side atomic frontend production build loop after startup without Vite.",
    )
    parser.add_argument(
        "--celery",
        action="store_true",
        help="Start the optional celery_worker in local development",
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
    FORCE_ENV = args.env or args.env_all
    should_build = args.build or args.refresh
    uses_host_frontend_build = frontend_dir.exists() and local_compose_uses_host_frontend_build(BASE_DIR)

    if args.vite and args.watch:
        print("[ERROR] --watch cannot be combined with --vite.")
        sys.exit(1)

    if args.watch and args.no_log_stream:
        print("[WARN] --watch has no effect together with --no-log-stream.")

    if args.refresh and not args.build:
        print("[WARN] --refresh is deprecated; use --build instead.")
        args.build = True

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

    if should_build:
        print("[INFO] Docker build ACTIVE: image rebuild will also refresh Python dependencies.")
        os.environ["UV_FLAGS"] = "--refresh"
    else:
        os.environ["UV_FLAGS"] = ""

    print(f"==================================================")
    print(
        f"[INFO] RUN-DEV | Mode: {MODE} | Env-Regen: {FORCE_ENV} | "
        f"Celery: {args.celery} | Build: {should_build} | Watch: {args.watch}"
    )
    print(f"==================================================")
    # DEBUG OUTPUT: Damit wir sehen, wo er sucht
    print(f"[DEBUG] Searching for frontend in: {frontend_dir}")

    # --- SCHRITT 1: ENV GENERATION ---
    if FORCE_ENV:
        print(f"[INFO] Generating .env for {env_mode}...")
        sys.argv = ["generate-env", "--env", env_mode]
        generate_env.main()

        frontend_env_path = frontend_dir / ".env"
        if frontend_dir.exists():
            shutil.copy(".env", frontend_env_path)
        
        if args.env_all:
            print("[INFO] Syncing GitHub secrets...")
            sys.argv = ["sync-secrets", "--target", "github"]
            sync_secrets.main()
    else:
        print("[INFO] Skipping .env generation (use --env or --env-all to force)")


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

    if args.celery and not args.edge and not args.spool and Path("docker-compose.local.yml").exists():
        os.environ["COMPOSE_PROFILES"] = "celery"
        print("[INFO] Local celery profile enabled.")
    else:
        os.environ.pop("COMPOSE_PROFILES", None)

    log_services = ["backend"]
    if args.celery:
        log_services.append("celery_worker")

    # --- SCHRITT 3: CLEANUP ---
    if args.spool:
        print("[INFO] Spool mode active, skipping local cleanup steps.")
    else:
        print("[INFO] Stopping containers...")
        run_command(["docker", "rm", "-f", "traefik"], ignore_errors=True)
        subprocess.run(
            ["docker", "rm", "-f", "traefik"], 
            stderr=subprocess.DEVNULL, 
            stdout=subprocess.DEVNULL, 
            shell=(sys.platform=="win32")
        )
        cleanup_optional_local_services(compose_files_args, args)


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
