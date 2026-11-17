import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from django_core_micha.scripts import isolated_test_start as starter
from django_core_micha.scripts import synthetic_test_fixture as fixture
from django_core_micha.test_management import local_proxy


IDENTITY = fixture.IDENTITY


def compose_config(identity, *, synthetic_local):
    backend = {
        "image": "innoservice-test-management:local" if identity == "innoservice" else "${TEST_MANAGEMENT_IMAGE:?missing isolated image}",
        "build": {"context": ".", "dockerfile": "backend/Dockerfile"},
        "command": ["python", "-m", "django_core_micha.test_management.isolated_entrypoint"],
        "environment": {
            "DJANGO_SETTINGS_MODULE": "backend.local_test_management_settings",
            "ENV_TYPE": "local" if synthetic_local else "${ENV_TYPE:?missing original environment identity}",
            "DB_HOST": "db", "DB_PORT": "5432", "DB_NAME": "${TEST_MANAGEMENT_DATABASE:?missing isolated database name}",
            "DB_USER": "test_management", "DB_PASSWORD": "${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}",
            "DJANGO_SECRET_KEY": "${TEST_MANAGEMENT_DJANGO_SECRET:?missing isolated application secret}",
            "TEST_MANAGEMENT_INSTANCE_ID": "${TEST_MANAGEMENT_INSTANCE_ID:?missing instance ID}",
            "TEST_MANAGEMENT_CONTROLLER_TOKEN": "${TEST_MANAGEMENT_CONTROLLER_TOKEN:?missing controller token}",
            "FRONTEND_BUILD_DIR": "/app/frontend_host/build_current",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        },
        "volumes": [
            "./backend:/app/backend:ro",
            "./frontend/build_current:/app/frontend_host/build_current:ro",
            {"type": "volume", "source": "media", "target": "/app/backend/media", "volume": {"nocopy": True}},
        ],
        "depends_on": {"db": {"condition": "service_healthy"}},
        "networks": ["isolated"],
    }
    if not synthetic_local:
        backend["env_file"] = [".env"]
    return {
        "services": {
            "backend": backend,
            "db": {
                "image": "postgres:18",
                "environment": {
                    "POSTGRES_DB": "${TEST_MANAGEMENT_DATABASE:?missing isolated database name}",
                    "POSTGRES_USER": "test_management",
                    "POSTGRES_PASSWORD": "${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}",
                },
                "volumes": [{"type": "volume", "source": "database", "target": "/var/lib/postgresql", "volume": {"nocopy": True}}],
                "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U test_management -d $${POSTGRES_DB}"], "interval": "2s", "timeout": "2s", "retries": 30},
                "networks": ["isolated"],
            },
        },
        "networks": {"isolated": {"internal": True, "ipam": {"config": [{"subnet": "${TEST_MANAGEMENT_SUBNET:?missing isolated network subnet}"}]}}},
        "volumes": {"database": {}, "media": {}},
    }


def app(tmp_path, identity=IDENTITY, *, synthetic_local=True, declaration=None, write_declaration=True, directory="fixture"):
    if synthetic_local:
        root = fixture.create_fixture(parent=tmp_path)
        if declaration is not None:
            (root / "test-management.consumer.json").write_text(json.dumps(declaration), encoding="utf-8")
        return root
    root = tmp_path / directory
    (root / "backend").mkdir(parents=True)
    (root / "frontend" / "build_current").mkdir(parents=True)
    (root / "frontend" / "build_current" / "index.html").write_text("fixture", encoding="utf-8")
    if declaration is None:
        declaration = {"version": 1, "identity": identity}
        if synthetic_local:
            declaration["mode"] = "synthetic-local"
    if write_declaration:
        (root / "test-management.consumer.json").write_text(json.dumps(declaration), encoding="utf-8")
    (root / "docker-compose.test-management.yml").write_text(
        starter.yaml.safe_dump(compose_config(identity, synthetic_local=synthetic_local), sort_keys=False),
        encoding="utf-8",
    )
    return root


def test_synthetic_local_declaration_accepts_literal_local_manifest_without_env_file(tmp_path):
    root = app(tmp_path)

    assert starter.consumer(root) == (root.resolve(), IDENTITY)
    starter.validate_compose_file(root / "docker-compose.test-management.yml", identity=IDENTITY)
    manifest = starter.yaml.safe_load((root / "docker-compose.test-management.yml").read_text(encoding="utf-8"))
    assert "env_file" not in manifest["services"]["backend"]
    assert manifest["services"]["backend"]["environment"]["ENV_TYPE"] == "local"
    dockerfile = (root / "backend/Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.13-slim\n")
    assert "innoservice" not in dockerfile
    assert "COPY shared-core /opt/shared-core" in dockerfile
    assert (root / "shared-core/django_core_micha/test_management/isolation.py").read_text(encoding="utf-8") == (
        fixture._CORE_ROOT / "test_management/isolation.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["copied-declaration", "source", "extra-file", "extra-directory", "env-file"])
def test_generated_contract_cannot_relabel_an_existing_or_modified_application(tmp_path, monkeypatch, change):
    root = app(tmp_path)
    if change == "copied-declaration":
        existing = tmp_path / "existing-staging-app"
        existing.mkdir()
        (existing / "test-management.consumer.json").write_text(
            (root / "test-management.consumer.json").read_text(encoding="utf-8"), encoding="utf-8")
        root = existing
    elif change == "source":
        (root / "backend/backend/local_test_management_settings.py").write_text('ENV_TYPE = "production"', encoding="utf-8")
    elif change == "extra-file":
        (root / "project.yaml").write_text("environment: unknown", encoding="utf-8")
    elif change == "extra-directory":
        (root / "existing-application").mkdir()
    else:
        manifest = root / "docker-compose.test-management.yml"
        config = starter.yaml.safe_load(manifest.read_text(encoding="utf-8"))
        config["services"]["backend"]["env_file"] = [".env"]
        manifest.write_text(starter.yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: pytest.fail("Docker must not be contacted"))
    with pytest.raises(starter.IsolationError):
        starter.start_isolated_instance(root, {})


def test_synthetic_compose_environment_disables_implicit_files_for_launcher_and_relay(tmp_path, monkeypatch):
    root = app(tmp_path)
    env = {"COMPOSE_ENV_FILES": "external-config", "COMPOSE_DISABLE_ENV_FILE": "0"}
    starter.consumer_compose_environment(root, env)
    assert env == {"COMPOSE_DISABLE_ENV_FILE": "1"}
    monkeypatch.setenv("COMPOSE_ENV_FILES", "external-config")
    monkeypatch.setenv("COMPOSE_DISABLE_ENV_FILE", "0")
    relay_env = local_proxy.compose_env(f"{IDENTITY}-tm-" + "a" * 32, root)
    assert relay_env["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert "COMPOSE_ENV_FILES" not in relay_env


@pytest.mark.parametrize("declaration", [
    {"version": 1, "identity": IDENTITY, "mode": "production"},
    {"version": 1, "identity": IDENTITY, "mode": "synthetic-local", "extra": "unknown"},
    {"version": 1, "identity": IDENTITY, "mode": True},
    {"version": 1, "identity": "innoservice", "mode": "synthetic-local"},
])
def test_malformed_or_reserved_synthetic_declarations_are_refused(tmp_path, declaration):
    root = app(tmp_path, declaration=declaration)

    with pytest.raises(starter.IsolationError):
        starter.consumer(root)


@pytest.mark.parametrize("path_kind", ["file", "link"])
def test_synthetic_consumer_rejects_any_existing_env_path_without_creating_one(tmp_path, monkeypatch, path_kind):
    root = app(tmp_path)
    env_path = root / ".env"
    real_lexists = starter.os.path.lexists
    calls = []

    def lexists(path):
        calls.append(Path(path))
        if Path(path) == env_path:
            return True  # Model a file or a link; the test never creates either.
        return real_lexists(path)

    monkeypatch.setattr(starter.os.path, "lexists", lexists)

    with pytest.raises(starter.IsolationError):
        starter.consumer(root)
    assert env_path in calls


@pytest.mark.parametrize("environment", ["production", "staging", "unknown"])
def test_synthetic_manifest_rejects_non_local_or_unknown_identity(tmp_path, environment):
    root = app(tmp_path)
    manifest = root / "docker-compose.test-management.yml"
    config = starter.yaml.safe_load(manifest.read_text(encoding="utf-8"))
    config["services"]["backend"]["environment"]["ENV_TYPE"] = environment
    manifest.write_text(starter.yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(starter.IsolationError):
        starter.validate_compose_file(manifest, identity=IDENTITY)


def test_ordinary_app_and_legacy_innoservice_keep_env_file_and_interpolated_identity_contract(tmp_path):
    ordinary = app(tmp_path, "ordinary-app", synthetic_local=False, directory="ordinary")
    ordinary_manifest = ordinary / "docker-compose.test-management.yml"
    starter.validate_compose_file(ordinary_manifest, identity="ordinary-app")
    ordinary_config = starter.yaml.safe_load(ordinary_manifest.read_text(encoding="utf-8"))
    ordinary_config["services"]["backend"]["environment"]["ENV_TYPE"] = "local"
    ordinary_manifest.write_text(starter.yaml.safe_dump(ordinary_config, sort_keys=False), encoding="utf-8")
    with pytest.raises(starter.IsolationError):
        starter.validate_compose_file(ordinary_manifest, identity="ordinary-app")

    legacy = app(tmp_path, "innoservice", synthetic_local=False, write_declaration=False, directory="innoservice")
    legacy_manifest = legacy / "docker-compose.test-management.yml"
    starter.validate_compose_file(legacy_manifest)
    legacy_config = starter.yaml.safe_load(legacy_manifest.read_text(encoding="utf-8"))
    legacy_config["services"]["backend"]["environment"]["ENV_TYPE"] = "staging"
    legacy_manifest.write_text(starter.yaml.safe_dump(legacy_config, sort_keys=False), encoding="utf-8")
    with pytest.raises(starter.IsolationError):
        starter.validate_compose_file(legacy_manifest)


@pytest.mark.parametrize("synthetic_local", [False, True])
@pytest.mark.parametrize("environment", ["production", "staging"])
def test_parent_non_local_environment_still_refuses_before_docker(tmp_path, monkeypatch, synthetic_local, environment):
    root = app(tmp_path, "ordinary-app" if not synthetic_local else IDENTITY, synthetic_local=synthetic_local)
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: pytest.fail("Docker must not be contacted"))

    with pytest.raises(starter.IsolationError):
        starter.start_isolated_instance(root, {"ENV_TYPE": environment})


def test_synthetic_contract_is_validated_for_start_resume_and_stop(tmp_path, monkeypatch):
    root = app(tmp_path)
    fixed = UUID("11111111-1111-1111-1111-111111111111")
    project = f"{IDENTITY}-tm-{fixed.hex}"
    validations = []
    commands = []
    environments = []
    ownership_checks = []
    validate = starter.validate_compose_file

    def validate_and_record(path, **kwargs):
        validations.append((Path(path), kwargs["identity"]))
        return validate(path, **kwargs)

    monkeypatch.setattr(starter, "validate_compose_file", validate_and_record)
    monkeypatch.setattr(starter.uuid, "uuid4", lambda: fixed)
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: None)
    monkeypatch.setattr(starter.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="no such volume"))
    def private(command, **kwargs):
        commands.append(command)
        environments.append(dict(kwargs["env"]))
        return 0

    def ownership(*args, **kwargs):
        ownership_checks.append((kwargs["identity"], kwargs["cwd"]))
        return None, "10.1.2.3"

    monkeypatch.setattr(starter, "_run_private", private)
    monkeypatch.setattr(starter, "_finish_start", lambda project, instance, compose, cwd, env, **kwargs: {"project": project})
    monkeypatch.setattr(starter, "validate_stopped_instance", ownership)
    monkeypatch.setattr(starter, "validate_runtime", ownership)
    monkeypatch.setattr(starter, "_docker_json", lambda *args, **kwargs: "healthy")

    assert starter.start_isolated_instance(root, {"ENV_TYPE": "local"})["project"] == project
    assert starter.resume_isolated_instance(root, project, {"ENV_TYPE": "local"})["project"] == project
    starter.stop_isolated_instance(root, project, {"ENV_TYPE": "local"})

    assert validations == [(root / "docker-compose.test-management.yml", IDENTITY)] * 2
    assert any(command[-2:] == ["start", "backend"] for command in commands)
    assert ["docker", "stop", project + "-backend-1", project + "-db-1"] in commands
    assert environments and all("ENV_TYPE" not in env for env in environments)
    assert all(env["COMPOSE_DISABLE_ENV_FILE"] == "1" for command, env in zip(commands, environments) if command[1] == "compose")
    assert ownership_checks == [(IDENTITY, root), (IDENTITY, root)]


@pytest.mark.parametrize("change", ["missing-build", "updated-core"])
@pytest.mark.parametrize("owned", [False, True])
def test_stop_checks_runtime_ownership_even_if_build_or_generated_source_changed(tmp_path, monkeypatch, change, owned):
    root = app(tmp_path)
    project = f"{IDENTITY}-tm-" + "a" * 32
    if change == "missing-build":
        (root / "frontend/build_current/index.html").unlink()
    else:
        monkeypatch.setitem(fixture.FILES, "shared-core/django_core_micha/__init__.py", "# newer core\n")
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)
    calls = []
    def runtime(actual_project, **kwargs):
        assert actual_project == project
        assert kwargs["identity"] == IDENTITY and kwargs["cwd"] == root
        calls.append("verify")
        if not owned:
            raise starter.IsolationError("foreign runtime")
    monkeypatch.setattr(starter, "validate_runtime", runtime)
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: None)
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: calls.append(command) or 0)
    if owned:
        starter.stop_isolated_instance(root, project, {})
        assert calls == ["verify", ["docker", "stop", project + "-backend-1", project + "-db-1"]]
    else:
        with pytest.raises(starter.IsolationError):
            starter.stop_isolated_instance(root, project, {})
        assert calls == ["verify"]


@pytest.mark.parametrize("contract_change", ["env-path", "non-local-manifest"])
def test_proxy_refuses_synthetic_contract_changes_before_forwarding(tmp_path, monkeypatch, contract_change):
    root = app(tmp_path)
    project = f"{IDENTITY}-tm-" + "a" * 32
    if contract_change == "env-path":
        env_path = root / ".env"
        real_lexists = starter.os.path.lexists
        monkeypatch.setattr(starter.os.path, "lexists", lambda path: Path(path) == env_path or real_lexists(path))
    else:
        manifest = root / "docker-compose.test-management.yml"
        config = starter.yaml.safe_load(manifest.read_text(encoding="utf-8"))
        config["services"]["backend"]["environment"]["ENV_TYPE"] = "staging"
        manifest.write_text(starter.yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    handler_class = local_proxy.handler_for(project, root)
    handler = handler_class.__new__(handler_class)
    handler.path, handler.command, handler.headers = "/api/healthz", "GET", {}
    handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
    errors, forwarded = [], []
    handler.send_error = lambda status: errors.append(status)
    handler.send_response = lambda status: forwarded.append(status)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    monkeypatch.setattr(local_proxy, "validate_runtime", lambda *args, **kwargs: pytest.fail("runtime must not be inspected"))
    monkeypatch.setattr(local_proxy.subprocess, "run", lambda *args, **kwargs: forwarded.append("exec"))

    handler.handle_app()

    assert errors == [502]
    assert forwarded == []
