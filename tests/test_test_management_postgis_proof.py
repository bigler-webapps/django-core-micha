import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.test import override_settings

from django_core_micha.scripts import isolated_test_start as starter
from django_core_micha.test_management import activation, isolated_entrypoint, isolation


INSTANCE = UUID("8f7d1d14-4d4e-41f8-b5d0-f79d8c09d3a6")
BOOT = "15f3f15b-8f4e-4b96-b2b2-6b13602d6ad3"
TOKEN = "synthetic-controller-token-" + "x" * 32
ADDRESS = "172.25.0.2"
MEDIA = "1 0 0:1 / /app/backend/media rw - ext4 /dev/sda rw"
PLAIN = "django.db.backends.postgresql"
GIS = "django.contrib.gis.db.backends.postgis"


@pytest.fixture
def evidence(tmp_path):
    boot, proof = tmp_path / "boot", tmp_path / "proof"
    boot.write_text(json.dumps({"boot_id": BOOT, "process_start": "123"}))
    env = {"ENV_TYPE": "local", "DJANGO_SETTINGS_MODULE": "backend.local_test_management_settings",
           "TEST_MANAGEMENT_INSTANCE_ID": str(INSTANCE), "TEST_MANAGEMENT_CONTROLLER_TOKEN": TOKEN,
           "DB_HOST": "db", "DB_PORT": "5432", "DB_USER": "test_management",
           "DB_NAME": f"dcm_tm_{INSTANCE.hex}", "DB_PASSWORD": "synthetic"}
    def write(database="postgresql"):
        data = {"instance": str(INSTANCE), "database_ip": ADDRESS, "boot_id": BOOT,
                "signature": isolation.signature(INSTANCE, ADDRESS, TOKEN, BOOT, database)}
        if database == "postgis":
            data["database"] = database
        proof.write_text(json.dumps(data))
        return data
    def verify(values=None):
        return isolation.verify_marker(values or env, MEDIA, proof, boot, "123", include_database=True)
    return env, proof, write, verify


def _activate(evidence, monkeypatch, engine):
    env, _, _, verify = evidence
    monkeypatch.setattr(activation, "verify_marker", lambda **kwargs: verify())
    monkeypatch.setenv("TEST_MANAGEMENT_CONTROLLER_TOKEN", TOKEN)
    monkeypatch.setenv("TEST_MANAGEMENT_DATABASE_FLAVOUR", "postgis")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): pass
        def fetchone(self): return (f"dcm_tm_{INSTANCE.hex}", ADDRESS, "/var/lib/postgresql/18/data", "1 0 0:1 / /var/lib/postgresql rw - ext4 /dev/sda rw")
    monkeypatch.setattr(activation, "connection", SimpleNamespace(cursor=Cursor))
    with override_settings(TEST_MANAGEMENT_ENABLED=True, ENV_TYPE="local", TEST_MANAGEMENT_ORIGINAL_ENV_TYPE="local",
                           TEST_MANAGEMENT_INSTANCE_ID=str(INSTANCE), TEST_MANAGEMENT_CONTROLLER_TOKEN=TOKEN,
                           TEST_MANAGEMENT_DATABASE_FLAVOUR="postgis", DATABASES={"default": {"ENGINE": engine, "USER": "test_management", "PORT": "5432", "HOST": "db", "NAME": env["DB_NAME"]}},
                           MEDIA_ROOT="/app/backend/media", DEBUG=False, SESSION_ENGINE="django.contrib.sessions.backends.db",
                           CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
                           CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
                           STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
                           CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=[], CORS_ALLOWED_ORIGIN_REGEXES=[], CORS_ALLOW_CREDENTIALS=False):
        return activation.validate_configuration()


def _refused(evidence, monkeypatch, engine):
    with pytest.raises(activation.TestManagementError) as error:
        _activate(evidence, monkeypatch, engine)
    assert error.value.code == "test_management_isolation_unverified"
    assert error.value.detail == "Test-management isolation has not been verified."


@pytest.mark.parametrize("flavour,engine,accepted", [
    ("postgresql", PLAIN, True), ("postgis", GIS, True),
    ("postgresql", GIS, False), ("postgis", PLAIN, False),
    ("postgis", "other.backend", False),
])
def test_activation_binds_engine_to_signed_flavour(evidence, monkeypatch, flavour, engine, accepted):
    evidence[2](flavour)
    if accepted:
        assert _activate(evidence, monkeypatch, engine).instance_id == INSTANCE
    else:
        _refused(evidence, monkeypatch, engine)


@pytest.mark.parametrize("mutation", ["remove", "plain", "unknown", "extra", "unsigned", "wrong_key"])
@pytest.mark.parametrize("engine", [PLAIN, GIS])
def test_forged_or_missing_flavour_gets_generic_activation_refusal(evidence, monkeypatch, mutation, engine):
    _, proof, write, _ = evidence
    data = write("postgis")
    if mutation == "remove": data.pop("database")
    if mutation == "plain": data["database"] = "postgresql"
    if mutation == "unknown": data["database"] = "other"
    if mutation == "extra": data["extra"] = True
    if mutation == "unsigned": data["signature"] = isolation.signature(INSTANCE, ADDRESS, TOKEN, BOOT)
    if mutation == "wrong_key": data["signature"] = isolation.signature(INSTANCE, ADDRESS, "other-token", BOOT, "postgis")
    proof.write_text(json.dumps(data))
    _refused(evidence, monkeypatch, engine)


@pytest.mark.parametrize("flavour,engine", [("postgresql", PLAIN), ("postgis", GIS)])
def test_installed_proof_reaches_the_activation_check(evidence, monkeypatch, flavour, engine):
    env, proof, _, verify = evidence
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(isolated_entrypoint, "BOOT_PATH", proof.parent / "boot")
    monkeypatch.setattr(isolated_entrypoint, "PROOF_PATH", proof)
    monkeypatch.setattr(isolated_entrypoint, "Path", lambda path: SimpleNamespace(read_text=lambda **kwargs: MEDIA))
    monkeypatch.setattr(isolated_entrypoint, "process_start", lambda: "123")
    monkeypatch.setattr(isolated_entrypoint, "database_evidence", lambda values: (
        f"dcm_tm_{INSTANCE.hex}", ADDRESS, "/var/lib/postgresql/18/data", "1 0 0:1 / /var/lib/postgresql rw - ext4 /dev/sda rw"))
    monkeypatch.setattr(isolated_entrypoint, "verify_marker", lambda: verify()[:2])
    isolated_entrypoint.install_proof(ADDRESS, flavour)
    assert _activate(evidence, monkeypatch, engine).instance_id == INSTANCE
    other = GIS if engine == PLAIN else PLAIN
    _refused(evidence, monkeypatch, other)


def test_proof_written_by_an_earlier_version_stays_valid(evidence):
    import hashlib, hmac
    _, proof, _, verify = evidence
    legacy = hmac.new(TOKEN.encode(), f"{INSTANCE}:{ADDRESS}:{BOOT}".encode(), hashlib.sha256).hexdigest()
    proof.write_text(json.dumps({"instance": str(INSTANCE), "database_ip": ADDRESS, "boot_id": BOOT, "signature": legacy}))
    assert verify() == (INSTANCE, ADDRESS, "postgresql")


@pytest.mark.parametrize("mutation", ["remove", "plain", "unknown", "extra", "unsigned", "wrong_key"])
def test_forged_postgis_marker_is_refused(evidence, mutation):
    _, proof, write, verify = evidence
    data = write("postgis")
    if mutation == "remove": data.pop("database")
    if mutation == "plain": data["database"] = "postgresql"
    if mutation == "unknown": data["database"] = "other"
    if mutation == "extra": data["extra"] = True
    if mutation == "unsigned": data["signature"] = isolation.signature(INSTANCE, ADDRESS, TOKEN, BOOT)
    if mutation == "wrong_key": data["signature"] = isolation.signature(INSTANCE, ADDRESS, "other-token", BOOT, "postgis")
    proof.write_text(json.dumps(data))
    with pytest.raises(isolation.IsolationFailure): verify()


@pytest.mark.parametrize("flavour", ["postgresql", "postgis"])
def test_starter_passes_declared_flavour_after_image_check(monkeypatch, tmp_path, flavour):
    image = starter._DATABASE_IMAGES[flavour]
    monkeypatch.setattr(starter, "_load_consumer", lambda *a, **k: (tmp_path, "alpha", False, flavour))
    monkeypatch.setattr(starter, "_docker_json", lambda *a, **k: image)
    monkeypatch.setattr(starter, "validate_runtime", lambda *a, **k: (None, ADDRESS))
    commands = []
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: commands.append(command) or 0)
    monkeypatch.setattr(starter, "_start_loopback_proxy", lambda *a: (SimpleNamespace(terminate=lambda: None), "1234"))
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(starter.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **k: Response()))
    starter._finish_start("alpha-tm-test", INSTANCE, ["compose"], tmp_path, {"TEST_MANAGEMENT_DATABASE_FLAVOUR": "forged"}, identity="alpha")
    # A plain instance keeps the pre-flavour call so consumers pinned to an older dcm still accept it.
    assert next(c for c in commands if "install-proof" in c)[-2:] == ([ADDRESS, "postgis"] if flavour == "postgis" else ["install-proof", ADDRESS])
    monkeypatch.setattr(starter, "_docker_json", lambda *a, **k: "foreign:image")
    commands.clear()
    with pytest.raises(starter.IsolationError): starter._finish_start("alpha-tm-test", INSTANCE, ["compose"], tmp_path, {}, identity="alpha")
    assert not commands


@pytest.mark.parametrize("argv,expected", [
    (["entry", "install-proof", ADDRESS], (ADDRESS,)),
    (["entry", "install-proof", ADDRESS, "postgis"], (ADDRESS, "postgis")),
])
def test_entrypoint_dispatches_valid_install_proof_arguments(monkeypatch, argv, expected):
    calls = []
    monkeypatch.setattr(isolated_entrypoint, "install_proof", lambda *args: calls.append(args))
    monkeypatch.setattr(isolated_entrypoint.sys, "argv", argv)
    isolated_entrypoint.main()
    assert calls == [expected]


def test_starter_refuses_a_database_image_change_during_startup(monkeypatch, tmp_path):
    image = starter._DATABASE_IMAGES["postgis"]
    images = iter([image, "foreign:image"])
    monkeypatch.setattr(starter, "_load_consumer", lambda *a, **k: (tmp_path, "alpha", False, "postgis"))
    monkeypatch.setattr(starter, "_docker_json", lambda *a, **k: next(images))
    monkeypatch.setattr(starter, "validate_runtime", lambda *a, **k: (None, ADDRESS))
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: 0)
    monkeypatch.setattr(starter, "_start_loopback_proxy", lambda *a: pytest.fail("proxy started after an image change"))
    with pytest.raises(starter.IsolationError):
        starter._finish_start("alpha-tm-test", INSTANCE, ["compose"], tmp_path, {}, identity="alpha")


def test_entrypoint_rejects_invalid_install_proof_arguments(monkeypatch):
    for argv in (["entry", "install-proof", ADDRESS, "postgis", "extra"], ["entry", "install-proof", ADDRESS, "other"], ["entry", "install-proof", ADDRESS, "postgresql"]):
        monkeypatch.setattr(isolated_entrypoint.sys, "argv", argv)
        with pytest.raises(SystemExit): isolated_entrypoint.main()
