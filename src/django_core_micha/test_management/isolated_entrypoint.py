"""Gate the local test server until the launcher has verified its resources."""

import json
import os
from pathlib import Path
import sys
import time
import uuid

from .isolation import (
    BOOT_PATH, IsolationFailure, PROOF_PATH, basic_evidence, process_start, signature,
    validate_database_evidence, verify_marker,
)


def database_evidence(env):
    import psycopg

    with psycopg.connect(
        host=env["DB_HOST"], port=int(env["DB_PORT"]), dbname=env["DB_NAME"],
        user=env["DB_USER"], password=env["DB_PASSWORD"], connect_timeout=5, autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), host(inet_server_addr()), current_setting('data_directory'), pg_read_file('/proc/self/mountinfo', 0, 1048576)")
            return cursor.fetchone()


def _atomic_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def install_proof(address: str):
    mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    instance, token = basic_evidence(os.environ, mountinfo)
    deadline = time.monotonic() + 30
    while True:
        try:
            if BOOT_PATH.is_symlink():
                raise IsolationFailure("Invalid boot evidence.")
            boot = json.loads(BOOT_PATH.read_text(encoding="utf-8"))
            if set(boot) == {"boot_id", "process_start"} and boot["process_start"] == process_start():
                boot_id = str(uuid.UUID(boot["boot_id"]))
                break
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise IsolationFailure("The instance did not prepare its boot evidence.")
        time.sleep(0.5)
    validate_database_evidence(instance, address, database_evidence(os.environ))
    if PROOF_PATH.is_symlink():
        raise IsolationFailure("Invalid isolation proof path.")
    proof = {"instance": str(instance), "database_ip": address, "boot_id": boot_id,
             "signature": signature(instance, address, token, boot_id)}
    _atomic_json(PROOF_PATH, proof)
    verify_marker()


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "install-proof":
            install_proof(sys.argv[2])
            print("isolation proof installed")
            return
        if len(sys.argv) != 1:
            raise IsolationFailure("Invalid bootstrap operation.")
        basic_evidence(os.environ, Path("/proc/self/mountinfo").read_text(encoding="utf-8"))
        deadline = time.monotonic() + 300
        boot_id = str(uuid.uuid4())
        while True:
            try:
                _atomic_json(BOOT_PATH, {"boot_id": boot_id, "process_start": process_start()})
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise IsolationFailure("The isolated media volume is not writable.")
                time.sleep(0.5)
        os.environ["TEST_MANAGEMENT_BOOT_ID"] = boot_id
        while time.monotonic() < deadline:
            try:
                verify_marker()
                os.execvp("daphne", ["daphne", "-b", "0.0.0.0", "-p", "8000", "backend.asgi:application"])
            except IsolationFailure:
                time.sleep(0.5)
        raise IsolationFailure("Isolation proof timed out.")
    except Exception:
        # Exception text may contain a DSN or other runtime configuration.
        print("local test instance isolation preflight failed", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
