"""Loopback-only, no-log browser relay for the isolated local instance."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .http_relay import HOP_HEADERS, MAX_BYTES, allowed_path
from django_core_micha.scripts.isolated_test_start import (
    IsolationError, _assert_local_docker, consumer, consumer_compose_environment, consumer_image, project_instance, validate_compose_file, validate_runtime,
)


def compose_env(project, cwd):
    cwd, identity = consumer(cwd)
    project_instance(project, identity)
    env = dict(os.environ)
    consumer_compose_environment(cwd, env)
    env.pop("COMPOSE_PROFILES", None)
    for key in ("ENV_TYPE", "TEST_MANAGEMENT_DATABASE_PASSWORD", "TEST_MANAGEMENT_DJANGO_SECRET", "TEST_MANAGEMENT_CONTROLLER_TOKEN"):
        env.pop(key, None)
    env.update({"TEST_MANAGEMENT_IMAGE": consumer_image(identity, cwd), "TEST_MANAGEMENT_DATABASE": "relay_placeholder", "TEST_MANAGEMENT_DATABASE_PASSWORD": "relay_placeholder",
                "TEST_MANAGEMENT_DJANGO_SECRET": "relay_placeholder", "TEST_MANAGEMENT_CONTROLLER_TOKEN": "relay_placeholder",
                "TEST_MANAGEMENT_INSTANCE_ID": "00000000-0000-0000-0000-000000000000", "TEST_MANAGEMENT_SUBNET": "10.0.0.0/24"})
    return env


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        # Request data and authentication cookies must never enter logs.
        return


def _runtime_snapshot(project):
    """Read only the fields used by validate_runtime, once per resource group."""
    container_fields = (".Config.Labels", ".Config.Image", ".State.Running", ".HostConfig.Privileged",
                        ".NetworkSettings.Networks", ".Id", ".Mounts", ".NetworkSettings.Ports",
                        ".HostConfig.PortBindings")
    groups = (
        ((f"{project}-backend-1", f"{project}-db-1"), container_fields),
        ((f"{project}_isolated",), (".Internal", ".Labels", ".Containers")),
        ((f"{project}_media", f"{project}_database"), (".Labels", ".Driver", ".Scope", ".Options")),
    )
    values = {}
    for names, fields in groups:
        # Do not inspect complete objects: container config may contain secrets.
        template = "[" + ",".join("{{json " + field + "}}" for field in fields) + "]"
        try:
            result = subprocess.run(["docker", "inspect", "--format", template, *names],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", timeout=15, check=True, shell=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise IsolationError("Unable to inspect isolated runtime.") from exc
        rows = result.stdout.splitlines()
        if len(rows) != len(names):
            raise IsolationError("Incomplete isolated runtime inspection.")
        for name, row in zip(names, rows):
            try:
                data = json.loads(row)
            except ValueError as exc:
                raise IsolationError("Invalid isolated runtime inspection.") from exc
            if not isinstance(data, list) or len(data) != len(fields):
                raise IsolationError("Invalid isolated runtime inspection.")
            values.update({(name, field): value for field, value in zip(fields, data)})

    def inspect(name, expression):
        try:
            return values[name, expression]
        except KeyError:
            raise IsolationError("Unexpected isolated runtime inspection.") from None
    return inspect


def handler_for(project, cwd):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def handle_app(self):
            if not allowed_path(self.path) or self.headers.get("Transfer-Encoding"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_BYTES:
                    raise ValueError
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError
                message = {"method": self.command, "path": self.path,
                           "headers": [[key, value] for key, value in self.headers.items()],
                           "body": base64.b64encode(body).decode("ascii")}
                checked_cwd, identity = consumer(cwd)
                project_instance(project, identity)
                validate_compose_file(checked_cwd / "docker-compose.test-management.yml", identity=identity)
                validate_runtime(project, inspect=_runtime_snapshot(project), identity=identity, cwd=checked_cwd)
                command = ["docker", "compose", "-p", project, "-f", "docker-compose.test-management.yml", "exec", "-T",
                           "backend", "python", "-m", "django_core_micha.test_management.http_relay"]
                result = subprocess.run(command, cwd=str(cwd), env=compose_env(project, cwd), input=json.dumps(message),
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                        encoding="utf-8", timeout=150, check=True, shell=False)
                response = json.loads(result.stdout)
                data = base64.b64decode(response["body"], validate=True)
                status = response["status"]
                if not isinstance(status, int) or not 100 <= status <= 599 or len(data) > MAX_BYTES:
                    raise ValueError
                self.send_response(status)
                for key, value in response["headers"]:
                    if key.lower() not in HOP_HEADERS | {"content-length", "server", "date"} and "\r" not in value and "\n" not in value:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
            except Exception:
                try:
                    self.send_error(502)
                except OSError:
                    pass

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = handle_app

    return Handler


def monitor(server, project):
    failures = 0
    while True:
        time.sleep(10)
        try:
            result = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", f"{project}-backend-1"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10, check=True)
            failures = 0 if result.stdout.strip() == "true" else failures + 1
        except (OSError, subprocess.SubprocessError):
            failures += 1
        if failures >= 3:
            server.shutdown()
            return


def main():
    if len(sys.argv) != 3:
        raise SystemExit(1)
    project = sys.argv[1]
    try:
        cwd, identity = consumer(Path(sys.argv[2]))
        project_instance(project, identity)
        validate_compose_file(cwd / "docker-compose.test-management.yml", identity=identity)
        docker_env = dict(os.environ)
        _assert_local_docker(cwd, docker_env)
        os.environ.pop("DOCKER_CONTEXT", None)
        os.environ["DOCKER_HOST"] = docker_env["DOCKER_HOST"]
        validate_runtime(project, identity=identity, cwd=cwd)
    except (IsolationError, OSError):
        raise SystemExit(1) from None
    server = QuietServer(("127.0.0.1", 0), handler_for(project, cwd))
    threading.Thread(target=monitor, args=(server, project), daemon=True).start()
    print(f"READY {server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
