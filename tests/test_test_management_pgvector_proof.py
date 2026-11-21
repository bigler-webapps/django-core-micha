import json
from types import SimpleNamespace

import pytest

from django_core_micha.scripts import isolated_test_start as starter
from django_core_micha.test_management import isolated_entrypoint, isolation
from tests import test_test_management_consumers as consumers
from tests import test_test_management_postgis_proof as proofs
from tests.test_test_management_postgis_proof import evidence


IMAGE = "pgvector/pgvector:pg18"


def test_pgvector_declaration_and_manifest(tmp_path):
    root = consumers.app(tmp_path, "app", "alpha")
    declaration = root / "test-management.consumer.json"
    manifest = root / "docker-compose.test-management.yml"
    declaration.write_text(json.dumps({"version": 1, "identity": "alpha", "database": "pgvector"}))
    assert starter._database_image(root, "alpha") == IMAGE
    for image in (IMAGE, "postgres:18", "postgis/postgis:18-3.6", "pgvector/pgvector:latest", "${IMAGE}"):
        manifest.write_text(consumers._manifest_text(image), encoding="utf-8")
        if image == IMAGE:
            starter.validate_compose_file(manifest, identity="alpha")
        else:
            with pytest.raises(starter.IsolationError):
                starter.validate_compose_file(manifest, identity="alpha")
    declaration.write_text(json.dumps({"version": 1, "identity": "alpha", "database": IMAGE}))
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)


@pytest.mark.parametrize("identity,mode", [("innoservice", None), ("alpha", "synthetic-local")])
def test_pgvector_excluded_consumers(tmp_path, identity, mode):
    consumers.test_database_declaration_forbidden_for_legacy_and_synthetic(tmp_path, identity, mode, "pgvector")


def test_pgvector_runtime_and_stopped_verification(tmp_path, monkeypatch):
    consumers.test_runtime_and_stopped_instance_verify_actual_resources(tmp_path, "alpha", "pgvector", IMAGE, monkeypatch)


def test_pgvector_proof_install_and_activation(evidence, monkeypatch):
    proofs.test_installed_proof_reaches_the_activation_check(evidence, monkeypatch, "pgvector", proofs.PLAIN)
    assert evidence[3]() == (proofs.INSTANCE, proofs.ADDRESS, "pgvector")
    proofs._refused(evidence, monkeypatch, proofs.GIS)


@pytest.mark.parametrize("mutation", ["remove", "plain", "postgis", "unknown", "extra", "unsigned", "wrong_key"])
def test_pgvector_forgery_refused(evidence, monkeypatch, mutation):
    _, proof, _, verify = evidence
    data = {"instance": str(proofs.INSTANCE), "database_ip": proofs.ADDRESS, "boot_id": proofs.BOOT,
            "database": "pgvector", "signature": isolation.signature(proofs.INSTANCE, proofs.ADDRESS, proofs.TOKEN, proofs.BOOT, "pgvector")}
    if mutation == "remove": data.pop("database")
    if mutation == "plain": data["database"] = "postgresql"
    if mutation == "postgis": data["database"] = "postgis"
    if mutation == "unknown": data["database"] = "other"
    if mutation == "extra": data["extra"] = True
    if mutation == "unsigned": data["signature"] = isolation.signature(proofs.INSTANCE, proofs.ADDRESS, proofs.TOKEN, proofs.BOOT)
    if mutation == "wrong_key": data["signature"] = isolation.signature(proofs.INSTANCE, proofs.ADDRESS, "wrong", proofs.BOOT, "pgvector")
    proof.write_text(json.dumps(data))
    with pytest.raises(isolation.IsolationFailure):
        verify()
    proofs._refused(evidence, monkeypatch, proofs.PLAIN)


def test_pgvector_bootstrap_checks_image_before_and_after(monkeypatch, tmp_path):
    monkeypatch.setattr(starter, "_load_consumer", lambda *a, **k: (tmp_path, "alpha", False, "pgvector"))
    monkeypatch.setattr(starter, "validate_runtime", lambda *a, **k: (None, proofs.ADDRESS))
    commands = []
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: commands.append(command) or 0)
    monkeypatch.setattr(starter, "_start_loopback_proxy", lambda *a: (SimpleNamespace(terminate=lambda: None), "1234"))
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(starter.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **k: Response()))
    monkeypatch.setattr(starter, "_docker_json", lambda *a, **k: IMAGE)
    starter._finish_start("alpha-tm-test", proofs.INSTANCE, ["compose"], tmp_path, {}, identity="alpha")
    assert next(c for c in commands if "install-proof" in c)[-2:] == [proofs.ADDRESS, "pgvector"]
    for images in (("postgres:18",), (IMAGE, "postgres:18")):
        sequence = iter(images)
        monkeypatch.setattr(starter, "_docker_json", lambda *a, **k: next(sequence))
        monkeypatch.setattr(starter, "_start_loopback_proxy", lambda *a: pytest.fail("proxy started"))
        with pytest.raises(starter.IsolationError):
            starter._finish_start("alpha-tm-test", proofs.INSTANCE, ["compose"], tmp_path, {}, identity="alpha")


def test_pgvector_entrypoint_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(isolated_entrypoint, "install_proof", lambda *args: calls.append(args))
    monkeypatch.setattr(isolated_entrypoint.sys, "argv", ["entry", "install-proof", proofs.ADDRESS, "pgvector"])
    isolated_entrypoint.main()
    assert calls == [(proofs.ADDRESS, "pgvector")]
