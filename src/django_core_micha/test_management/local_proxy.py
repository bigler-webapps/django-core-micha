"""Loopback-only, no-log browser relay for the isolated local instance."""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from .http_relay import HOP_HEADERS, MAX_BYTES, allowed_path

_PROJECT = re.compile(r"^innoservice-tm-[0-9a-f]{32}$")


def compose_env(project):
    env = dict(os.environ)
    for key in ("ENV_TYPE", "TEST_MANAGEMENT_DATABASE_PASSWORD", "TEST_MANAGEMENT_DJANGO_SECRET", "TEST_MANAGEMENT_CONTROLLER_TOKEN"):
        env.pop(key, None)
    env.update({"TEST_MANAGEMENT_DATABASE": "relay_placeholder", "TEST_MANAGEMENT_DATABASE_PASSWORD": "relay_placeholder",
                "TEST_MANAGEMENT_DJANGO_SECRET": "relay_placeholder", "TEST_MANAGEMENT_CONTROLLER_TOKEN": "relay_placeholder",
                "TEST_MANAGEMENT_INSTANCE_ID": "00000000-0000-0000-0000-000000000000", "TEST_MANAGEMENT_SUBNET": "10.0.0.0/24"})
    return env


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Request data and authentication cookies must never enter logs.
        return


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
                command = ["docker", "compose", "-p", project, "-f", "docker-compose.test-management.yml", "exec", "-T",
                           "backend", "python", "-m", "django_core_micha.test_management.http_relay"]
                result = subprocess.run(command, cwd=str(cwd), env=compose_env(project), input=json.dumps(message),
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
    if len(sys.argv) != 3 or not _PROJECT.fullmatch(sys.argv[1]):
        raise SystemExit(1)
    project, cwd = sys.argv[1], Path(sys.argv[2]).resolve()
    if cwd.name.lower() != "innoservice" or not (cwd / "docker-compose.test-management.yml").is_file():
        raise SystemExit(1)
    server = QuietServer(("127.0.0.1", 0), handler_for(project, cwd))
    threading.Thread(target=monitor, args=(server, project), daemon=True).start()
    print(f"READY {server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
