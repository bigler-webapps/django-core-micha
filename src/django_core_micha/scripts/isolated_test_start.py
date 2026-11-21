"""Validation helpers for the dedicated local test-management stack.

These helpers intentionally do not launch containers. Startup remains disabled
until runtime inspection and the database preflight have also succeeded.
"""

from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import ipaddress
import os
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

import yaml


class IsolationError(ValueError):
    pass


_IDENTITY = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_DATABASE_IMAGES = {"postgresql": "postgres:18", "postgis": "postgis/postgis:18-3.6", "pgvector": "pgvector/pgvector:pg18"}

_PROJECT = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-tm-([0-9a-f]{32})$")


def _load_consumer(cwd: Path, *, stop_only=False, include_database=False, include_build_args=False):
    """Load a versioned, deliberately small app-owned declaration."""
    try:
        cwd = cwd.resolve(strict=True)
        declaration = cwd / "test-management.consumer.json"
        if declaration.is_symlink():
            raise ValueError
        synthetic_local = False
        database = "postgresql"
        build_args = {}
        if declaration.exists():
            if declaration.is_symlink() or declaration.resolve().parent != cwd:
                raise ValueError
            data = json.loads(declaration.read_text(encoding="utf-8"), object_pairs_hook=_declaration_object)
            if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
                raise ValueError
            fields = {"version", "identity"}
            if not fields <= set(data) or set(data) - fields - {"mode", "database", "build_args"}:
                raise ValueError
            if "build_args" in data:
                build_args = data["build_args"]
                if not isinstance(build_args, dict) or len(build_args) > 8:
                    raise ValueError
                for name, value in build_args.items():
                    if (not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", name) or
                            any(word in name for word in ("TOKEN", "SECRET", "KEY", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")) or
                            name.startswith(("TEST_MANAGEMENT_", "DOCKER_", "COMPOSE_")) or
                            name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"} or
                            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", value)):
                        raise ValueError
            if "mode" in data:
                if data["mode"] != "synthetic-local":
                    raise ValueError
                synthetic_local = True
            if "database" in data:
                database = data["database"]
                if not isinstance(database, str) or database not in _DATABASE_IMAGES:
                    raise ValueError
            identity = data["identity"]
            if not isinstance(identity, str) or len(identity) > 30 or not _IDENTITY.fullmatch(identity):
                raise ValueError
            if identity == "innoservice" and cwd.name.lower() != "innoservice":
                raise ValueError
            if "database" in data and (synthetic_local or identity == "innoservice" or cwd.name.lower() == "innoservice"):
                raise ValueError
            if "build_args" in data and (synthetic_local or identity == "innoservice" or cwd.name.lower() == "innoservice"):
                raise ValueError
            if synthetic_local and (identity == "innoservice" or cwd.name.lower() == "innoservice"):
                raise ValueError
        elif cwd.name.lower() == "innoservice":
            identity = "innoservice"
        else:
            raise ValueError
        if synthetic_local and not stop_only and os.path.lexists(cwd / ".env"):
            raise ValueError
        if synthetic_local and not stop_only:
            from .synthetic_test_fixture import validate_fixture

            validate_fixture(cwd)
        for relative in (() if stop_only else ("docker-compose.test-management.yml", "backend", "frontend/build_current", "frontend/build_current/index.html")):
            path = cwd / relative
            if not path.exists() or not path.resolve().is_relative_to(cwd):
                raise ValueError
        if include_build_args:
            return cwd, identity, synthetic_local, database, build_args
        if include_database:
            return cwd, identity, synthetic_local, database
        return cwd, identity, synthetic_local
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IsolationError("Invalid or missing test-management consumer declaration.") from exc


def _database_image(cwd, identity, *, stop_only=False):
    if cwd is None:
        return _DATABASE_IMAGES["postgresql"]
    _, declared_identity, _, database = _load_consumer(cwd, stop_only=stop_only, include_database=True)
    if declared_identity != identity:
        raise IsolationError("Consumer identity does not match the declaration.")
    return _DATABASE_IMAGES[database]


def consumer(cwd: Path):
    cwd, identity, _ = _load_consumer(cwd)
    return cwd, identity


def consumer_compose_environment(cwd: Path, env: dict):
    """Keep generated fixtures independent of implicit Compose environment files."""
    _, _, synthetic_local = _load_consumer(cwd)
    if synthetic_local:
        env.pop("COMPOSE_ENV_FILES", None)
        env["COMPOSE_DISABLE_ENV_FILE"] = "1"


def project_instance(project: str, identity: str):
    match = _PROJECT.fullmatch(project)
    if not match or match.group(1) != identity:
        raise IsolationError("Invalid isolated instance handle for this consumer.")
    return uuid.UUID(hex=match.group(2))


def consumer_image(identity, cwd):
    directory = os.path.normcase(str(cwd.resolve())).encode("utf-8")
    _, declared_identity, _, _, build_args = _load_consumer(cwd, stop_only=True, include_build_args=True)
    if declared_identity != identity:
        raise IsolationError("Consumer identity does not match the declaration.")
    suffix = ""
    if build_args:
        canonical = json.dumps(build_args, sort_keys=True, separators=(",", ":")).encode("utf-8")
        suffix = "-" + hashlib.sha256(canonical).hexdigest()
    return f"{identity}-test-management-{hashlib.sha256(directory).hexdigest()}{suffix}:local"


def _image_matches(actual, identity, cwd):
    expected = consumer_image(identity, cwd) if cwd is not None else f"{identity}-test-management:local"
    return actual == expected or (identity == "innoservice" and actual == "innoservice-test-management:local")


@contextmanager
def _legacy_image_override(identity, cwd):
    if identity != "innoservice":
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="dcm-test-management-") as directory:
        path = Path(directory) / "image-override.yml"
        path.write_text(yaml.safe_dump({"services": {"backend": {"image": consumer_image(identity, cwd)}}}), encoding="utf-8")
        yield path


def _verify_directory(labels, cwd):
    if cwd is not None:
        recorded = labels.get("com.docker.compose.project.working_dir")
        if not isinstance(recorded, str) or Path(recorded).resolve() != cwd.resolve():
            raise IsolationError("The instance belongs to another application directory.")


def _verify_bind_sources(mounts, cwd):
    if cwd is not None:
        expected = {"/app/backend": cwd / "backend", "/app/frontend_host/build_current": cwd / "frontend" / "build_current"}
        for mount in mounts:
            if mount.get("Destination") in expected:
                if not isinstance(mount.get("Source"), str) or Path(mount["Source"]).resolve() != expected[mount["Destination"]].resolve():
                    raise IsolationError("A source mount belongs to another application directory.")


def _declaration_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate consumer declaration key.")
        result[key] = value
    return result


def _docker_json(name: str, expression: str, *, env=None):
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json " + expression + "}}", name],
            check=True, capture_output=True, text=True, timeout=15, env=env,
            shell=(sys.platform == "win32"),
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IsolationError("Unable to inspect the isolated container.") from exc


def _mountinfo(name: str, *, env=None) -> str:
    try:
        result = subprocess.run(["docker", "exec", name, "cat", "/proc/self/mountinfo"],
                                check=True, capture_output=True, text=True, timeout=15, env=env,
                                shell=(sys.platform == "win32"))
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationError("Unable to verify the isolated mount.") from exc


def _has_mount(mountinfo: str, target: str) -> bool:
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        if separator and len(fields) > 4 and fields[4] == target and bool(right.split()):
            return True
    return False


def validate_runtime(project: str, inspect=_docker_json, mountinfo=_mountinfo, *, identity="innoservice", cwd=None):
    """Verify actual per-instance volumes, loopback binding and exclusive network.

    The database image must equal the declared flavour, for every caller including stop.

    Docker inspection is restricted to named non-secret fields; container
    environments and Compose-expanded configuration are never inspected.
    """
    database_image = _database_image(cwd, identity, stop_only=True)
    project_instance(project, identity)
    backend, db = f"{project}-backend-1", f"{project}-db-1"
    network = f"{project}_isolated"
    try:
        for name, service in ((backend, "backend"), (db, "db")):
            labels = inspect(name, ".Config.Labels")
            _verify_directory(labels, cwd)
            if (labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != service or
                    not (_image_matches(inspect(name, ".Config.Image"), identity, cwd) if service == "backend" else inspect(name, ".Config.Image") == database_image) or inspect(name, ".State.Running") is not True or
                    inspect(name, ".HostConfig.Privileged") is not False):
                raise IsolationError("Container identity or state failed verification.")
            networks = inspect(name, ".NetworkSettings.Networks")
            if set(networks) != {network}:
                raise IsolationError("Container network membership failed verification.")
        if inspect(network, ".Internal") is not True:
            raise IsolationError("The instance network is not internal.")
        labels = inspect(network, ".Labels")
        if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.network") != "isolated":
            raise IsolationError("The instance network has unexpected ownership.")
        backend_id, db_id = inspect(backend, ".Id"), inspect(db, ".Id")
        members = inspect(network, ".Containers")
        if set(members) != {backend_id, db_id}:
            raise IsolationError("The instance network has unexpected endpoints.")
        db_address = str(ipaddress.ip_interface(members[db_id]["IPv4Address"]).ip)
        if inspect(db, ".NetworkSettings.Networks")[network]["IPAddress"] != db_address:
            raise IsolationError("The database network address does not match.")
        mounts = inspect(backend, ".Mounts")
        _verify_bind_sources(mounts, cwd)
        if len(mounts) != 3 or {item["Destination"] for item in mounts} != {"/app/backend", "/app/frontend_host/build_current", "/app/backend/media"}:
            raise IsolationError("The backend mount list is not isolated.")
        binds = [item for item in mounts if item["Destination"] != "/app/backend/media"]
        if any(item["Type"] != "bind" or item["RW"] is not False for item in binds):
            raise IsolationError("A backend source mount is not read-only.")
        media = next(item for item in mounts if item["Destination"] == "/app/backend/media")
        db_mounts = inspect(db, ".Mounts")
        if (media.get("Type") != "volume" or media.get("Name") != f"{project}_media" or media.get("RW") is not True or
                len(db_mounts) != 1 or db_mounts[0].get("Type") != "volume" or
                db_mounts[0].get("Destination") != "/var/lib/postgresql" or
                db_mounts[0].get("Name") != f"{project}_database" or db_mounts[0].get("RW") is not True):
            raise IsolationError("The instance volumes do not match their containers.")
        for suffix in ("media", "database"):
            volume = f"{project}_{suffix}"
            labels = inspect(volume, ".Labels")
            if (labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.volume") != suffix or
                    inspect(volume, ".Driver") != "local" or inspect(volume, ".Scope") != "local" or inspect(volume, ".Options")):
                raise IsolationError("A test volume has unexpected ownership or options.")
        if not _has_mount(mountinfo(backend), "/app/backend/media") or not _has_mount(mountinfo(db), "/var/lib/postgresql"):
            raise IsolationError("A test volume is not mounted inside its container.")
        ports = inspect(backend, ".NetworkSettings.Ports") or {}
        configured = inspect(backend, ".HostConfig.PortBindings") or {}
        if any(ports.values()) or any(configured.values()):
            raise IsolationError("The backend exposes a host port.")
        db_ports = inspect(db, ".NetworkSettings.Ports") or {}
        db_configured = inspect(db, ".HostConfig.PortBindings") or {}
        if any(db_ports.values()) or any(db_configured.values()):
            raise IsolationError("The database exposes a host port.")
        return None, db_address
    except IsolationError:
        raise
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise IsolationError("Runtime isolation verification failed.") from exc


def validate_stopped_instance(project: str, inspect=_docker_json, *, identity="innoservice", cwd=None) -> None:
    """Refuse to resume a live backend or a project with foreign volumes."""
    database_image = _database_image(cwd, identity, stop_only=True)
    project_instance(project, identity)
    backend, db = f"{project}-backend-1", f"{project}-db-1"
    try:
        for name, service in ((backend, "backend"), (db, "db")):
            labels = inspect(name, ".Config.Labels")
            _verify_directory(labels, cwd)
            if (labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != service or
                    not (_image_matches(inspect(name, ".Config.Image"), identity, cwd) if service == "backend" else inspect(name, ".Config.Image") == database_image) or inspect(name, ".HostConfig.Privileged") is not False):
                raise ValueError
        if inspect(backend, ".State.Running") is not False:
            raise IsolationError("The instance is still running; refusing to take it over.")
        mounts, db_mounts = inspect(backend, ".Mounts"), inspect(db, ".Mounts")
        _verify_bind_sources(mounts, cwd)
        if len(mounts) != 3 or {m["Destination"] for m in mounts} != {"/app/backend", "/app/frontend_host/build_current", "/app/backend/media"}:
            raise ValueError
        for item in mounts:
            if item["Destination"] == "/app/backend/media":
                if item.get("Type") != "volume" or item.get("Name") != f"{project}_media" or item.get("RW") is not True:
                    raise ValueError
            elif item.get("Type") != "bind" or item.get("RW") is not False:
                raise ValueError
        if (len(db_mounts) != 1 or db_mounts[0].get("Type") != "volume" or
                db_mounts[0].get("Destination") != "/var/lib/postgresql" or
                db_mounts[0].get("Name") != f"{project}_database" or db_mounts[0].get("RW") is not True):
            raise ValueError
        for suffix in ("media", "database"):
            volume = f"{project}_{suffix}"
            labels = inspect(volume, ".Labels")
            if (labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.volume") != suffix or
                    inspect(volume, ".Driver") != "local" or inspect(volume, ".Scope") != "local" or inspect(volume, ".Options")):
                raise ValueError
    except IsolationError:
        raise
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise IsolationError("The stopped instance ownership could not be verified.") from exc


def validate_compose_file(path: Path, *, identity="innoservice", stop_only=False) -> None:
    """Reject a manifest that could reuse ordinary storage or expose a service."""
    try:
        declaration = path.parent / "test-management.consumer.json"
        synthetic_local = False
        database_image = _DATABASE_IMAGES["postgresql"]
        build_args = {}
        if declaration.is_symlink() or declaration.exists():
            cwd, declared_identity, synthetic_local, database, build_args = _load_consumer(path.parent, stop_only=stop_only, include_build_args=True)
            database_image = _DATABASE_IMAGES[database]
            if declared_identity != identity or path.resolve() != (cwd / "docker-compose.test-management.yml").resolve():
                raise ValueError
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or set(config) != {"services", "networks", "volumes"}:
            raise ValueError
        if config["volumes"] != {"database": {}, "media": {}}:
            raise ValueError
        services = config["services"]
        if set(services) != {"backend", "db"} or config["networks"] != {"isolated": {"internal": True, "ipam": {"config": [{"subnet": "${TEST_MANAGEMENT_SUBNET:?missing isolated network subnet}"}]}}}:
            raise ValueError
        backend, db = services["backend"], services["db"]
        backend_fields = {"image", "build", "command", "environment", "volumes", "depends_on", "networks"}
        if not synthetic_local:
            backend_fields.add("env_file")
        if set(backend) != backend_fields:
            raise ValueError
        if set(db) != {"image", "environment", "volumes", "healthcheck", "networks"}:
            raise ValueError
        image = "innoservice-test-management:local" if identity == "innoservice" else "${TEST_MANAGEMENT_IMAGE:?missing isolated image}"
        build = {"context": ".", "dockerfile": "backend/Dockerfile"}
        if build_args:
            build["args"] = build_args
        if backend["image"] != image or backend["build"] != build:
            raise ValueError
        if backend["command"] != ["python", "-m", "django_core_micha.test_management.isolated_entrypoint"]:
            raise ValueError
        if not synthetic_local and backend["env_file"] != [".env"]:
            raise ValueError
        if backend["volumes"] != ["./backend:/app/backend:ro", "./frontend/build_current:/app/frontend_host/build_current:ro", {"type": "volume", "source": "media", "target": "/app/backend/media", "volume": {"nocopy": True}}]:
            raise ValueError
        if db["volumes"] != [{"type": "volume", "source": "database", "target": "/var/lib/postgresql", "volume": {"nocopy": True}}]:
            raise ValueError
        if backend["networks"] != ["isolated"] or db["networks"] != ["isolated"]:
            raise ValueError
        if backend["depends_on"] != {"db": {"condition": "service_healthy"}} or db["image"] != database_image:
            raise ValueError
        if backend["environment"] != {
            "DJANGO_SETTINGS_MODULE": "backend.local_test_management_settings",
            "ENV_TYPE": "local" if synthetic_local else "${ENV_TYPE:?missing original environment identity}",
            "DB_HOST": "db", "DB_PORT": "5432", "DB_NAME": "${TEST_MANAGEMENT_DATABASE:?missing isolated database name}",
            "DB_USER": "test_management", "DB_PASSWORD": "${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}",
            "DJANGO_SECRET_KEY": "${TEST_MANAGEMENT_DJANGO_SECRET:?missing isolated application secret}",
            "TEST_MANAGEMENT_INSTANCE_ID": "${TEST_MANAGEMENT_INSTANCE_ID:?missing instance ID}",
            "TEST_MANAGEMENT_CONTROLLER_TOKEN": "${TEST_MANAGEMENT_CONTROLLER_TOKEN:?missing controller token}",
            "FRONTEND_BUILD_DIR": "/app/frontend_host/build_current",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        }:
            raise ValueError
        if db["environment"] != {
            "POSTGRES_DB": "${TEST_MANAGEMENT_DATABASE:?missing isolated database name}",
            "POSTGRES_USER": "test_management",
            "POSTGRES_PASSWORD": "${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}",
        }:
            raise ValueError
        if db["healthcheck"] != {"test": ["CMD-SHELL", "pg_isready -U test_management -d $${POSTGRES_DB}"], "interval": "2s", "timeout": "2s", "retries": 30}:
            raise ValueError
    except (OSError, TypeError, KeyError, ValueError, yaml.YAMLError) as exc:
        raise IsolationError("The standalone test-management Compose manifest is not the verified layout.") from exc


_DIAGNOSTIC_PATTERNS = (
    ("address pools have been fully subnetted", "Docker has no free local network address pool."),
    ("could not find an available, non-overlapping", "Docker has no free local network address pool."),
    ("pool overlaps", "The isolated Docker network overlaps an existing network."),
    ("all predefined address pools", "Docker has no free local network address pool."),
    ("no space left on device", "Insufficient disk space for the isolated stack."),
    ("not enough space", "Insufficient disk space for the isolated stack."),
    ("out of memory", "Insufficient memory for the isolated stack."),
    ("permission denied", "Docker denied access while preparing the isolated stack."),
    ("mounts denied", "Docker denied a required local mount."),
    ("invalid mount config", "Docker rejected an isolated mount configuration."),
    ("port is already allocated", "The isolated loopback port is unavailable."),
    ("failed to solve", "The isolated image build failed."),
    ("pull access denied", "A required container image is unavailable."),
    ("no such file or directory", "A required isolated startup file is unavailable."),
    ("error response from daemon", "Docker rejected an isolated startup operation."),
)
_DOCKER_ERROR_CATEGORIES = (
    ("mount", "mount"), ("volume", "volume"), ("network", "network"),
    ("port", "port"), ("path", "path"), ("permission", "permission"),
    ("not a directory", "not-a-directory"), ("is a directory", "is-a-directory"),
    ("already exists", "already-exists"), ("not found", "not-found"),
    ("no such", "not-found"), ("invalid", "invalid"), ("read-only", "read-only"),
    ("driver", "driver"), ("container", "container"), ("image", "image"),
)


def _run_private(command, *, cwd, env, timeout=60, heartbeat=False):
    """Run without exposing Compose output, runtime configuration or credentials."""
    try:
        process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", shell=False)
        diagnostic = {"message": None, "daemon_error": False, "categories": set()}

        def inspect_output():
            tail = ""
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    return
                sample = (tail + chunk).lower()
                if diagnostic["message"] is None:
                    diagnostic["message"] = next((message for pattern, message in _DIAGNOSTIC_PATTERNS if pattern in sample), None)
                if "error response from daemon" in sample:
                    diagnostic["daemon_error"] = True
                    sample = sample[sample.rfind("error response from daemon"):]
                if diagnostic["daemon_error"]:
                    diagnostic["categories"].update(label for pattern, label in _DOCKER_ERROR_CATEGORIES if pattern in sample)
                tail = sample[-256:]

        reader = threading.Thread(target=inspect_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        next_update = time.monotonic() + 30
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.terminate()
                raise IsolationError("The isolated startup step timed out.")
            if heartbeat and time.monotonic() >= next_update:
                print("[INFO] Preparing the isolated test instance...", flush=True)
                next_update = time.monotonic() + 30
            time.sleep(0.5)
        reader.join(timeout=5)
        if process.returncode and diagnostic["message"]:
            print(f"[WARN] {diagnostic['message']}", flush=True)
        if process.returncode and diagnostic["categories"]:
            print(f"[WARN] Docker error categories: {', '.join(sorted(diagnostic['categories']))}.", flush=True)
        return process.returncode
    except OSError as exc:
        raise IsolationError("Unable to run the isolated startup step.") from exc


def _docker_output(command, *, cwd, env):
    try:
        result = subprocess.run(command, cwd=str(cwd), env=env, check=True, capture_output=True,
                                text=True, timeout=15, shell=False)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationError("Unable to verify the local Docker daemon.") from exc


def _assert_local_docker(cwd: Path, env: dict) -> None:
    host = (_docker_output(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], cwd=cwd, env=env)
            if env.get("DOCKER_CONTEXT") else
            env.get("DOCKER_HOST") or _docker_output(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], cwd=cwd, env=env))
    if not (host.startswith("unix:///") or host.lower().startswith("npipe:////./pipe/")):
        raise IsolationError("Test management requires a local Docker daemon.")
    env.pop("DOCKER_CONTEXT", None)
    env["DOCKER_HOST"] = host
    _docker_output(["docker", "info", "--format", "{{.ServerVersion}}"], cwd=cwd, env=env)


def _start_loopback_proxy(project, cwd, env):
    proxy_env = dict(env)
    for key in ("TEST_MANAGEMENT_CONTROLLER_TOKEN", "TEST_MANAGEMENT_DATABASE_PASSWORD", "TEST_MANAGEMENT_DJANGO_SECRET"):
        proxy_env.pop(key, None)
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if sys.platform == "win32" else 0
    try:
        process = subprocess.Popen([sys.executable, "-m", "django_core_micha.test_management.local_proxy", project, str(cwd)],
                                   cwd=str(cwd), env=proxy_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", shell=False,
                                   creationflags=flags, start_new_session=(sys.platform != "win32"))
    except OSError as exc:
        raise IsolationError("The local browser relay could not be started.") from exc
    lines = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        line = lines.get(timeout=15).strip()
        match = re.fullmatch(r"READY ([0-9]{1,5})", line)
        if not match or not 1 <= int(match.group(1)) <= 65535:
            raise ValueError
        return process, match.group(1)
    except (queue.Empty, ValueError) as exc:
        process.terminate()
        raise IsolationError("The local browser relay did not become ready.") from exc


def _finish_start(project, instance, compose, cwd, env, *, identity, renew=False):
    inspect = lambda name, expression: _docker_json(name, expression, env=env)
    mountinfo = lambda name: _mountinfo(name, env=env)
    _, declared_identity, _, database = _load_consumer(cwd, include_database=True)
    if declared_identity != identity:
        raise IsolationError("Consumer identity does not match the declaration.")
    _, database_ip = validate_runtime(project, inspect=inspect, mountinfo=mountinfo, identity=identity, cwd=cwd)
    if inspect(f"{project}-db-1", ".Config.Image") != _DATABASE_IMAGES[database]:
        raise IsolationError("The database image does not match the declared flavour.")
    for command in (["chown", "1000:1000", "/app/backend/media"], ["chmod", "0700", "/app/backend/media"]):
        if _run_private(compose + ["exec", "-T", "--user", "0", "backend"] + command, cwd=cwd, env=env):
            raise IsolationError("The isolated media volume could not be prepared.")
    if _run_private(compose + ["exec", "-T", "backend", "python", "-m", "django_core_micha.test_management.isolated_entrypoint", "install-proof", database_ip] + ([database] if database != "postgresql" else []), cwd=cwd, env=env):
        raise IsolationError("The database isolation preflight failed.")
    if _run_private(compose + ["exec", "-T", "backend", "python", "manage.py", "migrate", "--noinput"], cwd=cwd, env=env, timeout=300, heartbeat=True):
        raise IsolationError("The isolated database migration failed.")
    if renew and _run_private(compose + ["exec", "-T", "backend", "python", "manage.py", "renew_test_controller"], cwd=cwd, env=env):
        raise IsolationError("The isolated controller could not be renewed.")
    _, final_ip = validate_runtime(project, inspect=inspect, mountinfo=mountinfo, identity=identity, cwd=cwd)
    if final_ip != database_ip or inspect(f"{project}-db-1", ".Config.Image") != _DATABASE_IMAGES[database]:
        raise IsolationError("The isolated runtime changed during startup.")
    proxy, port = _start_loopback_proxy(project, cwd, env)
    url = f"http://127.0.0.1:{port}/api/healthz"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=15) as response:
                if response.status == 200:
                    print(f"READY http://127.0.0.1:{port}/", flush=True)
                    return {"instance": str(instance), "project": project, "base_url": f"http://127.0.0.1:{port}"}
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    proxy.terminate()
    raise IsolationError("The isolated backend did not become ready.")


def _stop_owned_candidate(project, identity, compose, cwd, env):
    """Stop only services whose project and checkout ownership can be proved.

    Isolation validation may itself have failed, so cleanup must not depend on
    the unsafe property being valid. Unknown or missing services are untouched.
    """
    project_instance(project, identity)
    try:
        validate_compose_file(cwd / "docker-compose.test-management.yml", identity=identity)
    except IsolationError:
        print("[WARN] Candidate cleanup refused because its manifest changed.", flush=True)
        return
    for service in ("backend", "db"):
        try:
            labels = _docker_json(f"{project}-{service}-1", ".Config.Labels", env=env)
            if (labels.get("com.docker.compose.project") != project or
                    labels.get("com.docker.compose.service") != service):
                continue
            _verify_directory(labels, cwd)
            if _run_private(compose + ["stop", service], cwd=cwd, env=env, timeout=60):
                print("[WARN] An owned candidate service could not be stopped.", flush=True)
        except (IsolationError, AttributeError, TypeError):
            continue


def start_isolated_instance(cwd: Path, inherited_env=None):
    """Start one uniquely named instance after static and runtime isolation checks."""
    env = dict(os.environ if inherited_env is None else inherited_env)
    if env.get("ENV_TYPE") and env["ENV_TYPE"].lower() != "local":
        raise IsolationError("The caller environment is not local.")
    # Compose resolves the app's own original identity; an inherited shell
    # value must not mask a staging/production value from the app configuration.
    env.pop("ENV_TYPE", None)
    cwd, identity = consumer(cwd)
    manifest = cwd / "docker-compose.test-management.yml"
    validate_compose_file(manifest, identity=identity)
    consumer_compose_environment(cwd, env)
    _assert_local_docker(cwd, env)
    instance = uuid.uuid4()
    project = f"{identity}-tm-{instance.hex}"
    env.update({
        "TEST_MANAGEMENT_IMAGE": consumer_image(identity, cwd),
        "TEST_MANAGEMENT_INSTANCE_ID": str(instance),
        "TEST_MANAGEMENT_SUBNET": f"10.{instance.bytes[0]}.{instance.bytes[1]}.0/24",
        "TEST_MANAGEMENT_DATABASE": f"dcm_tm_{instance.hex}",
        "TEST_MANAGEMENT_DATABASE_PASSWORD": secrets.token_urlsafe(32),
        "TEST_MANAGEMENT_DJANGO_SECRET": secrets.token_urlsafe(48),
        "TEST_MANAGEMENT_CONTROLLER_TOKEN": secrets.token_urlsafe(32),
    })
    env.pop("COMPOSE_PROFILES", None)
    for suffix in ("database", "media"):
        name = f"{project}_{suffix}"
        try:
            result = subprocess.run(["docker", "volume", "inspect", name], cwd=str(cwd), env=env,
                                    capture_output=True, text=True, timeout=15, shell=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise IsolationError("Unable to verify a fresh test volume.") from exc
        if result.returncode == 0:
            raise IsolationError("A generated test volume already exists; refusing reuse.")
        if "no such volume" not in result.stderr.lower():
            raise IsolationError("Unable to verify a fresh test volume.")
    with _legacy_image_override(identity, cwd) as override:
        compose = ["docker", "compose", "-p", project, "-f", str(manifest)]
        if override is not None:
            compose += ["-f", str(override)]
        print(f"[INFO] Isolated instance: {project}", flush=True)
        try:
            if _run_private(compose + ["up", "-d", "--build"], cwd=cwd, env=env, timeout=1800, heartbeat=True):
                raise IsolationError("The isolated containers could not be started.")
            return _finish_start(project, instance, compose, cwd, env, identity=identity)
        except Exception:
            # A failed candidate is stopped but never deletes volumes or other instances.
            _stop_owned_candidate(project, identity, compose, cwd, env)
            raise


def resume_isolated_instance(cwd: Path, project: str, inherited_env=None):
    """Explicitly resume one stopped instance without taking over a live one."""
    cwd, identity = consumer(cwd)
    instance = project_instance(project, identity)
    env = dict(os.environ if inherited_env is None else inherited_env)
    if env.get("ENV_TYPE") and env["ENV_TYPE"].lower() != "local":
        raise IsolationError("The caller environment is not local.")
    env.pop("ENV_TYPE", None)
    env.pop("COMPOSE_PROFILES", None)
    manifest = cwd / "docker-compose.test-management.yml"
    validate_compose_file(manifest, identity=identity)
    consumer_compose_environment(cwd, env)
    _assert_local_docker(cwd, env)
    validate_stopped_instance(project, inspect=lambda name, expression: _docker_json(name, expression, env=env), identity=identity, cwd=cwd)
    # Compose needs placeholders to parse the manifest for `start`/`exec`.
    # Those operations retain the original container environment and credentials.
    env.update({"TEST_MANAGEMENT_IMAGE": consumer_image(identity, cwd), "TEST_MANAGEMENT_INSTANCE_ID": str(instance), "TEST_MANAGEMENT_SUBNET": f"10.{instance.bytes[0]}.{instance.bytes[1]}.0/24", "TEST_MANAGEMENT_DATABASE": f"dcm_tm_{instance.hex}",
                "TEST_MANAGEMENT_DATABASE_PASSWORD": "resume-placeholder", "TEST_MANAGEMENT_DJANGO_SECRET": "resume-placeholder",
                "TEST_MANAGEMENT_CONTROLLER_TOKEN": "resume-placeholder"})
    compose = ["docker", "compose", "-p", project, "-f", str(manifest)]
    print(f"[INFO] Resuming isolated instance: {project}", flush=True)
    try:
        if _run_private(compose + ["start", "db"], cwd=cwd, env=env):
            raise IsolationError("The isolated database could not be resumed.")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if _docker_json(f"{project}-db-1", ".State.Health.Status", env=env) == "healthy":
                break
            time.sleep(1)
        else:
            raise IsolationError("The isolated database did not become ready.")
        if _run_private(compose + ["start", "backend"], cwd=cwd, env=env):
            raise IsolationError("The isolated backend could not be resumed.")
        return _finish_start(project, instance, compose, cwd, env, identity=identity, renew=True)
    except Exception:
        _stop_owned_candidate(project, identity, compose, cwd, env)
        raise


def stop_isolated_instance(cwd: Path, project: str, inherited_env=None):
    """Stop only the verified instance; preserve its data for explicit recovery."""
    # Stopping an owned runtime must remain possible after source/build changes.
    # No Compose parsing or synthetic code execution is needed for this operation.
    cwd, identity, _, _, build_args = _load_consumer(cwd, stop_only=True, include_build_args=True)
    if build_args:
        # Only consumers with declared build arguments: the manifest must still carry exactly them.
        validate_compose_file(cwd / "docker-compose.test-management.yml", identity=identity, stop_only=True)
    project_instance(project, identity)
    env = dict(os.environ if inherited_env is None else inherited_env)
    if env.get("ENV_TYPE") and env["ENV_TYPE"].lower() != "local":
        raise IsolationError("The caller environment is not local.")
    env.pop("ENV_TYPE", None)
    env.pop("COMPOSE_PROFILES", None)
    _assert_local_docker(cwd, env)
    inspect = lambda name, expression: _docker_json(name, expression, env=env)
    validate_runtime(project, inspect=inspect, mountinfo=lambda name: _mountinfo(name, env=env), identity=identity, cwd=cwd)
    command = ["docker", "stop", f"{project}-backend-1", f"{project}-db-1"]
    if _run_private(command, cwd=cwd, env=env, timeout=60):
        raise IsolationError("The isolated instance could not be stopped.")
    print(f"[INFO] Stopped isolated instance: {project}", flush=True)
