import json
from uuid import UUID

import pytest

from django_core_micha.test_management.isolation import (
    IsolationFailure, basic_evidence, signature, validate_database_evidence, verify_marker,
)
from django_core_micha.test_management import isolated_entrypoint


INSTANCE = UUID("8f7d1d14-4d4e-41f8-b5d0-f79d8c09d3a6")
TOKEN = "synthetic-controller-token-" + "x" * 32
MEDIA = "1 0 0:1 / /app/backend/media rw - ext4 /dev/sda rw"
DATABASE = "2 0 0:2 / /var/lib/postgresql rw - ext4 /dev/sda rw"


def test_isolated_server_prefers_daphne_and_falls_back_to_uvicorn(monkeypatch):
    monkeypatch.setattr(isolated_entrypoint.shutil, "which", lambda name: f"/bin/{name}")
    assert isolated_entrypoint.server_command() == ["daphne", "-b", "0.0.0.0", "-p", "8000", "backend.asgi:application"]
    monkeypatch.setattr(isolated_entrypoint.shutil, "which", lambda name: "/bin/uvicorn" if name == "uvicorn" else None)
    assert isolated_entrypoint.server_command() == ["uvicorn", "--host", "0.0.0.0", "--port", "8000", "backend.asgi:application"]
    monkeypatch.setattr(isolated_entrypoint.shutil, "which", lambda name: None)
    with pytest.raises(IsolationFailure):
        isolated_entrypoint.server_command()


def test_unverified_isolation_never_executes_a_server(monkeypatch):
    entrypoint = isolated_entrypoint
    monkeypatch.setenv("TEST_MANAGEMENT_BOOT_ID", "original")
    monkeypatch.setattr(entrypoint.sys, "argv", ["isolated_entrypoint"])
    monkeypatch.setattr(entrypoint, "basic_evidence", lambda *_: None)
    monkeypatch.setattr(entrypoint.Path, "read_text", lambda *_args, **_kwargs: "mount-info")
    monkeypatch.setattr(entrypoint, "_atomic_json", lambda *_: None)
    monkeypatch.setattr(entrypoint, "process_start", lambda: "1")
    clock = iter([0, 1, 302])
    monkeypatch.setattr(entrypoint.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(entrypoint.time, "sleep", lambda *_: None)
    monkeypatch.setattr(entrypoint, "verify_marker", lambda: (_ for _ in ()).throw(IsolationFailure()))
    monkeypatch.setattr(entrypoint.os, "execvp", lambda *_: pytest.fail("server launched before proof"))
    with pytest.raises(SystemExit):
        entrypoint.main()


def test_verified_isolation_executes_uvicorn_fallback(monkeypatch):
    entrypoint = isolated_entrypoint
    monkeypatch.setenv("TEST_MANAGEMENT_BOOT_ID", "original")
    monkeypatch.setattr(entrypoint.sys, "argv", ["isolated_entrypoint"])
    monkeypatch.setattr(entrypoint, "basic_evidence", lambda *_: None)
    monkeypatch.setattr(entrypoint.Path, "read_text", lambda *_args, **_kwargs: "mount-info")
    monkeypatch.setattr(entrypoint, "_atomic_json", lambda *_: None)
    monkeypatch.setattr(entrypoint, "process_start", lambda: "1")
    monkeypatch.setattr(entrypoint, "verify_marker", lambda: None)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: "/bin/uvicorn" if name == "uvicorn" else None)
    launched = []
    monkeypatch.setattr(entrypoint.os, "execvp", lambda executable, args: (launched.append((executable, args)), (_ for _ in ()).throw(SystemExit(0))))
    with pytest.raises(SystemExit):
        entrypoint.main()
    assert launched == [("uvicorn", ["uvicorn", "--host", "0.0.0.0", "--port", "8000", "backend.asgi:application"])]


def env():
    return {"ENV_TYPE": "local", "DJANGO_SETTINGS_MODULE": "backend.local_test_management_settings",
            "TEST_MANAGEMENT_INSTANCE_ID": str(INSTANCE), "TEST_MANAGEMENT_CONTROLLER_TOKEN": TOKEN,
            "DB_HOST": "db", "DB_PORT": "5432", "DB_USER": "test_management",
            "DB_NAME": f"dcm_tm_{INSTANCE.hex}", "DB_PASSWORD": "synthetic-password"}


def test_marker_requires_instance_bound_signature_and_media_mount(tmp_path):
    proof = tmp_path / "proof"
    boot = tmp_path / "boot"
    boot_id = "15f3f15b-8f4e-4b96-b2b2-6b13602d6ad3"
    boot.write_text(json.dumps({"boot_id": boot_id, "process_start": "123"}), encoding="utf-8")
    proof.write_text(json.dumps({"instance": str(INSTANCE), "database_ip": "172.25.0.2",
                                 "boot_id": boot_id, "signature": signature(INSTANCE, "172.25.0.2", TOKEN, boot_id)}), encoding="utf-8")
    assert verify_marker(env(), MEDIA, proof, boot, "123") == (INSTANCE, "172.25.0.2")
    with pytest.raises(IsolationFailure):
        verify_marker({**env(), "TEST_MANAGEMENT_CONTROLLER_TOKEN": "x" * 40}, MEDIA, proof, boot, "123")
    with pytest.raises(IsolationFailure):
        verify_marker(env(), MEDIA.replace("/app/backend/media", "/unrelated"), proof, boot, "123")
    with pytest.raises(IsolationFailure):
        verify_marker(env(), MEDIA, proof, boot, "124")
    with pytest.raises(IsolationFailure):
        verify_marker({**env(), "TEST_MANAGEMENT_BOOT_ID": str(UUID(int=1))}, MEDIA, proof, boot, "123")


@pytest.mark.parametrize("change", [
    {"ENV_TYPE": "staging"}, {"ENV_TYPE": "production"}, {"ENV_TYPE": ""},
    {"DB_NAME": "existing-development"}, {"DB_HOST": "production-host"},
    {"TEST_MANAGEMENT_CONTROLLER_TOKEN": "short"},
])
def test_basic_evidence_refuses_wrong_environment_or_database(change):
    with pytest.raises(IsolationFailure):
        basic_evidence({**env(), **change}, MEDIA)


def test_evidence_accepts_a_different_consumer_settings_module():
    assert basic_evidence({**env(), "DJANGO_SETTINGS_MODULE": "another_app.local_test_settings"}, MEDIA) == (INSTANCE, TOKEN)
    with pytest.raises(IsolationFailure):
        basic_evidence({**env(), "DJANGO_SETTINGS_MODULE": ""}, MEDIA)


def test_database_evidence_requires_matching_peer_database_and_mount():
    valid = (f"dcm_tm_{INSTANCE.hex}", "172.25.0.2", "/var/lib/postgresql/18/docker", DATABASE)
    validate_database_evidence(INSTANCE, "172.25.0.2", valid)
    for invalid in (("existing", *valid[1:]), (valid[0], "172.25.0.3", *valid[2:]),
                    (*valid[:3], DATABASE.replace("/var/lib/postgresql", "/unrelated")), (*valid[:2], "/existing", DATABASE)):
        with pytest.raises(IsolationFailure):
            validate_database_evidence(INSTANCE, "172.25.0.2", invalid)
