"""Local instance evidence shared by startup and each management request."""

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import uuid


class IsolationFailure(ValueError):
    pass


MEDIA_PATH = "/app/backend/media"
PROOF_PATH = Path(MEDIA_PATH) / ".test-management-proof"
BOOT_PATH = Path(MEDIA_PATH) / ".test-management-boot"


def has_mount(mountinfo: str, target: str) -> bool:
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        if separator and len(fields) > 4 and fields[4] == target and bool(right.split()):
            return True
    return False


def basic_evidence(env, mountinfo: str):
    try:
        instance = uuid.UUID(env.get("TEST_MANAGEMENT_INSTANCE_ID", ""))
        token = env.get("TEST_MANAGEMENT_CONTROLLER_TOKEN", "")
        if (env.get("ENV_TYPE", "").lower() != "local" or
                not env.get("DJANGO_SETTINGS_MODULE") or
                env.get("DB_HOST") != "db" or env.get("DB_PORT") != "5432" or
                env.get("DB_USER") != "test_management" or env.get("DB_NAME") != f"dcm_tm_{instance.hex}" or
                len(token) < 32 or "\n" in token or "\r" in token or
                not env.get("DB_PASSWORD") or not has_mount(mountinfo, MEDIA_PATH)):
            raise ValueError
        return instance, token
    except (ValueError, TypeError, AttributeError) as exc:
        raise IsolationFailure("The local instance evidence is incomplete.") from exc


def process_start() -> str:
    # /proc/1/stat field 22 is the kernel start tick of this container's PID 1.
    return Path("/proc/1/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]


def signature(instance, address: str, token: str, boot_id: str, database="postgresql") -> str:
    if database not in ("postgresql", "postgis"):
        raise ValueError("Invalid database flavour.")
    message = f"{instance}:{address}:{boot_id}"
    if database == "postgis":
        message += ":postgis"
    return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_marker(env=None, mountinfo=None, proof_path=PROOF_PATH, boot_path=BOOT_PATH, start_tick=None, *, include_database=False):
    env = os.environ if env is None else env
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8") if mountinfo is None else mountinfo
        instance, token = basic_evidence(env, mountinfo)
        if proof_path.is_symlink() or boot_path.is_symlink():
            raise ValueError
        boot = json.loads(boot_path.read_text(encoding="utf-8"))
        expected_start = process_start() if start_tick is None else start_tick
        if set(boot) != {"boot_id", "process_start"} or boot["process_start"] != expected_start:
            raise ValueError
        boot_id = str(uuid.UUID(boot["boot_id"]))
        if env.get("TEST_MANAGEMENT_BOOT_ID") and env["TEST_MANAGEMENT_BOOT_ID"] != boot_id:
            raise ValueError
        data = json.loads(proof_path.read_text(encoding="utf-8"))
        fields = {"instance", "database_ip", "boot_id", "signature"}
        if set(data) == fields:
            database = "postgresql"
        elif set(data) == fields | {"database"} and data["database"] == "postgis":
            database = "postgis"
        else:
            raise ValueError
        if data["instance"] != str(instance) or data["boot_id"] != boot_id:
            raise ValueError
        address = str(ipaddress.ip_address(data["database_ip"]))
        if (not ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback or
                not hmac.compare_digest(data["signature"], signature(instance, address, token, boot_id, database))):
            raise ValueError
        return (instance, address, database) if include_database else (instance, address)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, json.JSONDecodeError) as exc:
        raise IsolationFailure("The local isolation proof is invalid.") from exc


def validate_database_evidence(instance, address: str, row) -> None:
    try:
        name, actual_address, data_directory, mountinfo = row
        if (name != f"dcm_tm_{instance.hex}" or actual_address != address or
                not data_directory.startswith("/var/lib/postgresql/") or
                not has_mount(mountinfo, "/var/lib/postgresql")):
            raise ValueError
    except (TypeError, ValueError, AttributeError) as exc:
        raise IsolationFailure("The database isolation proof is invalid.") from exc
