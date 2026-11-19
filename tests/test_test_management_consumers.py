import json
import io
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from django_core_micha.scripts import isolated_test_start as starter
from django_core_micha.test_management import local_proxy


def app(tmp_path, name, identity=None):
    root = tmp_path / name
    (root / "backend").mkdir(parents=True)
    (root / "frontend" / "build_current").mkdir(parents=True)
    (root / "frontend" / "build_current" / "index.html").write_text("test")
    (root / "docker-compose.test-management.yml").write_text("services: {}")
    if identity is not None:
        (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": identity}))
    return root


@pytest.mark.parametrize("identity", ["alpha", "beta-app"])
def test_declared_consumers_have_isolated_handles_and_images(tmp_path, identity):
    root = app(tmp_path, identity, identity)
    cwd, actual = starter.consumer(root)
    assert actual == identity
    instance = starter.project_instance(f"{identity}-tm-" + "a" * 32, identity)
    assert instance.hex == "a" * 32
    assert identity in starter.consumer_image(identity, cwd)
    with pytest.raises(starter.IsolationError):
        starter.project_instance(f"other-tm-" + "a" * 32, identity)


def test_same_identity_in_two_directories_does_not_share_image(tmp_path):
    first = app(tmp_path / "parent-one", "app", "example")
    second = app(tmp_path / "parent-two", "app", "example")
    assert starter.consumer_image("example", first) != starter.consumer_image("example", second)


def test_browser_proxy_uses_consumer_image_and_rejects_foreign_handle(tmp_path):
    root = app(tmp_path, "one", "alpha")
    env = local_proxy.compose_env("alpha-tm-" + "a" * 32, root)
    assert env["TEST_MANAGEMENT_IMAGE"] == starter.consumer_image("alpha", root)
    with pytest.raises(starter.IsolationError):
        local_proxy.compose_env("beta-tm-" + "a" * 32, root)


@pytest.mark.parametrize("valid", [True, False])
def test_proxy_checks_runtime_before_forwarding(tmp_path, monkeypatch, valid):
    root = app(tmp_path, "app", "alpha")
    project = "alpha-tm-" + "a" * 32
    handler_class = local_proxy.handler_for(project, root)
    handler = handler_class.__new__(handler_class)
    handler.path, handler.command, handler.headers = "/api/healthz", "GET", {}
    handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
    errors, calls = [], []
    handler.send_error = lambda status: errors.append(status)
    handler.send_response = lambda status: calls.append(status)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    monkeypatch.setattr(local_proxy, "validate_compose_file", lambda *args, **kwargs: None)
    snapshot = object()
    monkeypatch.setattr(local_proxy, "_runtime_snapshot", lambda *args: snapshot)
    def runtime(*args, **kwargs):
        assert kwargs["inspect"] is snapshot
        if not valid:
            raise starter.IsolationError("unverified")
    monkeypatch.setattr(local_proxy, "validate_runtime", runtime)
    def execute(*args, **kwargs):
        calls.append("exec")
        return SimpleNamespace(stdout=json.dumps({"status": 200, "headers": [], "body": "b2s="}))
    monkeypatch.setattr(local_proxy.subprocess, "run", execute)
    handler.handle_app()
    assert ("exec" in calls) == valid
    assert errors == ([] if valid else [502])
    if valid:
        assert handler.wfile.getvalue() == b"ok"


def test_innoservice_legacy_declaration_and_handle(tmp_path):
    root = app(tmp_path, "innoservice")
    assert starter.consumer(root)[1] == "innoservice"
    assert starter._image_matches("innoservice-test-management:local", "innoservice", root)
    assert starter._image_matches(starter.consumer_image("innoservice", root), "innoservice", root)
    assert starter.project_instance("innoservice-tm-" + "1" * 32, "innoservice").hex == "1" * 32


def test_legacy_new_builds_are_scoped_by_checkout(tmp_path):
    first = app(tmp_path / "first", "innoservice")
    second = app(tmp_path / "second", "innoservice")
    with starter._legacy_image_override("innoservice", first) as override:
        config = starter.yaml.safe_load(override.read_text(encoding="utf-8"))
        assert config == {"services": {"backend": {"image": starter.consumer_image("innoservice", first)}}}
    assert starter.consumer_image("innoservice", first) != starter.consumer_image("innoservice", second)


@pytest.mark.parametrize("declaration", [None, {}, {"version": True, "identity": "alpha"},
    {"version": 1, "identity": "../alpha"}, {"version": 1, "identity": "Alpha"},
    {"version": 1, "identity": "innoservice"}, {"version": 1, "identity": "alpha", "extra": 1}])
def test_unknown_or_malformed_consumer_is_refused(tmp_path, declaration):
    root = app(tmp_path, "test-app")
    if declaration is not None:
        (root / "test-management.consumer.json").write_text(json.dumps(declaration))
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)


def test_duplicate_declaration_keys_are_rejected(tmp_path):
    root = app(tmp_path, "test-app")
    (root / "test-management.consumer.json").write_text('{"version":1,"identity":"alpha","identity":"beta"}')
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)


@pytest.mark.parametrize("environment", ["production", "staging", "unknown"])
def test_non_local_environment_fails_before_docker(tmp_path, monkeypatch, environment):
    root = app(tmp_path, "test-app", "alpha")
    monkeypatch.setattr(starter, "validate_compose_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: pytest.fail("Docker must not be contacted"))
    with pytest.raises(starter.IsolationError):
        starter.start_isolated_instance(root, {"ENV_TYPE": environment})


def test_remote_docker_is_refused(tmp_path):
    with pytest.raises(starter.IsolationError):
        starter._assert_local_docker(tmp_path, {"DOCKER_HOST": "tcp://example.invalid:2375"})


def test_failed_candidate_cleanup_stops_only_owned_services(tmp_path, monkeypatch):
    root = app(tmp_path, "test-app", "alpha")
    project = "alpha-tm-" + "1" * 32
    calls = []
    monkeypatch.setattr(starter, "validate_compose_file", lambda *args, **kwargs: None)
    def inspect(name, expression, *, env):
        service = "backend" if "backend" in name else "db"
        owner = root if service == "backend" else tmp_path / "foreign"
        return {"com.docker.compose.project": project, "com.docker.compose.service": service,
                "com.docker.compose.project.working_dir": str(owner)}
    monkeypatch.setattr(starter, "_docker_json", inspect)
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: calls.append(command) or 0)
    starter._stop_owned_candidate(project, "alpha", ["docker", "compose"], root, {})
    assert calls == [["docker", "compose", "stop", "backend"]]


@pytest.mark.parametrize("identity", ["alpha", "beta"])
def test_declared_consumer_start_resume_and_stop_use_the_same_identity(tmp_path, monkeypatch, identity):
    root = app(tmp_path, identity, identity)
    fixed = UUID("11111111-1111-1111-1111-111111111111")
    project = identity + "-tm-" + fixed.hex
    commands = []
    monkeypatch.setattr(starter.uuid, "uuid4", lambda: fixed)
    monkeypatch.setattr(starter, "validate_compose_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *args: None)
    monkeypatch.setattr(starter.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="no such volume"))
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: commands.append(command) or 0)
    monkeypatch.setattr(starter, "_finish_start", lambda project, instance, compose, cwd, env, **kwargs: {"project": project})
    monkeypatch.setattr(starter, "validate_stopped_instance", lambda *args, **kwargs: None)
    monkeypatch.setattr(starter, "validate_runtime", lambda *args, **kwargs: (None, "10.1.2.3"))
    monkeypatch.setattr(starter, "_docker_json", lambda *args, **kwargs: "healthy")
    assert starter.start_isolated_instance(root, {"ENV_TYPE": "local"})["project"] == project
    assert starter.resume_isolated_instance(root, project, {"ENV_TYPE": "local"})["project"] == project
    starter.stop_isolated_instance(root, project, {"ENV_TYPE": "local"})
    assert all(any(project in argument for argument in command) for command in commands)
    assert any(command[-2:] == ["start", "backend"] for command in commands)
    assert ["docker", "stop", project + "-backend-1", project + "-db-1"] in commands
    with pytest.raises(starter.IsolationError):
        starter.resume_isolated_instance(root, "foreign-tm-" + fixed.hex, {"ENV_TYPE": "local"})


def test_failed_runtime_isolation_prevents_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(starter, "validate_runtime", lambda *args, **kwargs: (_ for _ in ()).throw(starter.IsolationError("invalid isolation")))
    monkeypatch.setattr(starter, "_run_private", lambda *args, **kwargs: pytest.fail("no command may run before verification"))
    with pytest.raises(starter.IsolationError):
        starter._finish_start("alpha-tm-" + "1" * 32, UUID("11111111-1111-1111-1111-111111111111"), [], tmp_path, {}, identity="alpha")


@pytest.mark.parametrize("legacy", [False, True])
def test_owned_stop_does_not_require_a_current_frontend_build(tmp_path, monkeypatch, legacy):
    identity = "innoservice" if legacy else "alpha"
    root = app(tmp_path, identity, None if legacy else identity)
    (root / "frontend/build_current/index.html").unlink()
    project = identity + "-tm-" + "1" * 32
    calls = []
    def verify(actual, **kwargs):
        assert actual == project and kwargs["identity"] == identity and kwargs["cwd"] == root
        calls.append("verified")
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: None)
    monkeypatch.setattr(starter, "validate_runtime", verify)
    monkeypatch.setattr(starter, "_run_private", lambda command, **kwargs: calls.append(command) or 0)
    starter.stop_isolated_instance(root, project, {})
    assert calls == ["verified", ["docker", "stop", project + "-backend-1", project + "-db-1"]]


def test_directory_and_bind_ownership_fail_closed(tmp_path):
    first, second = app(tmp_path, "one", "alpha"), app(tmp_path, "two", "alpha")
    starter._verify_directory({"com.docker.compose.project.working_dir": str(first)}, first)
    with pytest.raises(starter.IsolationError):
        starter._verify_directory({"com.docker.compose.project.working_dir": str(second)}, first)
    with pytest.raises(starter.IsolationError):
        starter._verify_directory({}, first)
    mounts = [{"Destination": "/app/backend", "Source": str(first / "backend")},
              {"Destination": "/app/frontend_host/build_current", "Source": str(first / "frontend/build_current")}]
    starter._verify_bind_sources(mounts, first)
    mounts[0]["Source"] = str(second / "backend")
    with pytest.raises(starter.IsolationError):
        starter._verify_bind_sources(mounts, first)


def test_manifest_contract_supports_legacy_and_declared_consumer(tmp_path):
    original = Path(__file__).resolve().parents[2] / "innoservice" / "docker-compose.test-management.yml"
    if not original.is_file():
        pytest.skip("Innoservice checkout not available")
    starter.validate_compose_file(original)
    text = original.read_text(encoding="utf-8").replace("innoservice-test-management:local", "${TEST_MANAGEMENT_IMAGE:?missing isolated image}")
    manifest = tmp_path / "docker-compose.test-management.yml"
    manifest.write_text(text, encoding="utf-8")
    starter.validate_compose_file(manifest, identity="alpha")
    with pytest.raises(starter.IsolationError):
        starter.validate_compose_file(manifest, identity="innoservice")


@pytest.mark.parametrize("identity,database,image", [("alpha", "postgresql", "postgres:18"), ("beta", "postgis", "postgis/postgis:18-3.6")])
def test_runtime_and_stopped_instance_verify_actual_resources(tmp_path, identity, database, image, monkeypatch):
    root = app(tmp_path, identity, identity)
    (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": identity, "database": database}))
    project = identity + "-tm-" + "1" * 32
    backend, db, network = project + "-backend-1", project + "-db-1", project + "_isolated"
    values = {}
    for name, service, image in ((backend, "backend", starter.consumer_image(identity, root)), (db, "db", image)):
        values[name, ".Config.Labels"] = {"com.docker.compose.project": project, "com.docker.compose.service": service,
                                           "com.docker.compose.project.working_dir": str(root)}
        values[name, ".Config.Image"] = image
        values[name, ".State.Running"] = True
        values[name, ".HostConfig.Privileged"] = False
        values[name, ".NetworkSettings.Networks"] = {network: {"IPAddress": "10.1.2.3"}}
        values[name, ".Id"] = service + "-id"
        values[name, ".NetworkSettings.Ports"] = {}
    values[backend, ".HostConfig.PortBindings"] = {}
    values[db, ".HostConfig.PortBindings"] = {}
    values[network, ".Internal"] = True
    values[network, ".Labels"] = {"com.docker.compose.project": project, "com.docker.compose.network": "isolated"}
    values[network, ".Containers"] = {"backend-id": {"IPv4Address": "10.1.2.4/24"}, "db-id": {"IPv4Address": "10.1.2.3/24"}}
    values[backend, ".Mounts"] = [
        {"Destination": "/app/backend", "Source": str(root / "backend"), "Type": "bind", "RW": False},
        {"Destination": "/app/frontend_host/build_current", "Source": str(root / "frontend/build_current"), "Type": "bind", "RW": False},
        {"Destination": "/app/backend/media", "Name": project + "_media", "Type": "volume", "RW": True}]
    values[db, ".Mounts"] = [{"Destination": "/var/lib/postgresql", "Name": project + "_database", "Type": "volume", "RW": True}]
    for suffix in ("media", "database"):
        volume = project + "_" + suffix
        values[volume, ".Labels"] = {"com.docker.compose.project": project, "com.docker.compose.volume": suffix}
        values[volume, ".Driver"] = "local"
        values[volume, ".Scope"] = "local"
        values[volume, ".Options"] = None
    inspect = lambda name, expression: values[name, expression]
    mountinfo = lambda name: "1 2 3:4 / /app/backend/media rw - ext4 /dev/test rw\n1 2 3:4 / /var/lib/postgresql rw - ext4 /dev/test rw"
    assert starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)[1] == "10.1.2.3"
    import re
    def batch_inspect(command, **kwargs):
        fields = re.findall(r"\{\{json ([^}]+)\}\}", command[3])
        return SimpleNamespace(stdout="\n".join(json.dumps([values[name, field] for field in fields]) for name in command[4:]))
    monkeypatch.setattr(local_proxy.subprocess, "run", batch_inspect)
    snapshot = local_proxy._runtime_snapshot(project)
    assert starter.validate_runtime(project, inspect=snapshot, mountinfo=mountinfo, identity=identity, cwd=root)[1] == "10.1.2.3"
    for bad_image in ("postgres:18" if database == "postgis" else "postgis/postgis:18-3.6", "postgis/postgis:18-3.5", "postgis/postgis@sha256:" + "a" * 64, "foreign:18"):
        values[db, ".Config.Image"] = bad_image
        with pytest.raises(starter.IsolationError):
            starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)
        values[backend, ".State.Running"] = False
        with pytest.raises(starter.IsolationError):
            starter.validate_stopped_instance(project, inspect, identity=identity, cwd=root)
        values[backend, ".State.Running"] = True
    values[db, ".Config.Image"] = image
    original_source = values[backend, ".Mounts"][0]["Source"]
    values[backend, ".Mounts"][0]["Source"] = str(tmp_path / "foreign/backend")
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime(project, inspect=local_proxy._runtime_snapshot(project), mountinfo=mountinfo, identity=identity, cwd=root)
    values[backend, ".Mounts"][0]["Source"] = original_source
    values[network, ".Labels"]["com.docker.compose.project"] = "foreign"
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)
    values[network, ".Labels"]["com.docker.compose.project"] = project
    values[db, ".HostConfig.PortBindings"] = {"5432/tcp": [{"HostPort": "5432"}]}
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)
    values[db, ".HostConfig.PortBindings"] = {}
    values[backend, ".State.Running"] = False
    starter.validate_stopped_instance(project, inspect, identity=identity, cwd=root)
    values[backend, ".Mounts"][0]["Source"] = str(tmp_path / "foreign/backend")
    with pytest.raises(starter.IsolationError):
        starter.validate_stopped_instance(project, inspect, identity=identity, cwd=root)
    values[backend, ".Mounts"][0]["Source"] = original_source
    values[backend, ".Config.Labels"]["com.docker.compose.project.working_dir"] = str(tmp_path / "foreign")
    with pytest.raises(starter.IsolationError):
        starter.validate_stopped_instance(project, inspect, identity=identity, cwd=root)
    values[backend, ".State.Running"] = True
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)


@pytest.mark.parametrize("value,expected", [(None, "postgres:18"), ("postgresql", "postgres:18"), ("postgis", "postgis/postgis:18-3.6")])
def test_database_declaration_allowed(tmp_path, value, expected):
    root = app(tmp_path, "test-app", "alpha")
    declaration = {"version": 1, "identity": "alpha"}
    if value is not None:
        declaration["database"] = value
    (root / "test-management.consumer.json").write_text(json.dumps(declaration))
    assert starter._database_image(root, "alpha") == expected


@pytest.mark.parametrize("extra", [{"database": "unknown"}, {"database": 1}, {"database": []}, {"database": "postgis/postgis:18-3.6"}, {"image": "postgis/postgis:18-3.6"}, {"database": "postgis", "image": "postgres:18"}])
def test_database_declaration_rejected_before_docker(tmp_path, monkeypatch, extra):
    root = app(tmp_path, "test-app", "alpha")
    (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": "alpha", **extra}))
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *args: pytest.fail("Docker contacted"))
    with pytest.raises(starter.IsolationError):
        starter.start_isolated_instance(root, {})


@pytest.mark.parametrize("database", ["postgis", "postgresql"])
@pytest.mark.parametrize("identity,mode", [("innoservice", None), ("alpha", "synthetic-local")])
def test_database_declaration_forbidden_for_legacy_and_synthetic(tmp_path, identity, mode, database):
    root = app(tmp_path, identity, identity)
    data = {"version": 1, "identity": identity, "database": database}
    if mode:
        data["mode"] = mode
    (root / "test-management.consumer.json").write_text(json.dumps(data))
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)


@pytest.mark.parametrize("database,image", [("postgresql", "postgres:18"), ("postgis", "postgis/postgis:18-3.6")])
def test_database_manifest_matches_declaration(tmp_path, database, image):
    root = app(tmp_path, "test-app", "alpha")
    (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": "alpha", "database": database}))
    manifest = root / "docker-compose.test-management.yml"
    manifest.write_text(_manifest_text(image), encoding="utf-8")
    starter.validate_compose_file(manifest, identity="alpha")
    for bad in ("postgres:18" if database == "postgis" else "postgis/postgis:18-3.6", "postgis/postgis:18-3.5", "postgis/postgis@sha256:" + "a" * 64, "foreign:18"):
        manifest.write_text(_manifest_text(bad), encoding="utf-8")
        with pytest.raises(starter.IsolationError):
            starter.validate_compose_file(manifest, identity="alpha")


def test_postgis_declaration_does_not_make_a_non_declaring_app_eligible(tmp_path):
    root = app(tmp_path, "other-app", None)
    (root / "docker-compose.test-management.yml").write_text(_manifest_text("postgis/postgis:18-3.6"), encoding="utf-8")
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime("other-tm-" + "1" * 32, lambda *_: pytest.fail("Docker contacted"), identity="other", cwd=root)


def _manifest_text(database_image):
    import yaml

    return yaml.safe_dump({
        "services": {
            "backend": {
                "image": "${TEST_MANAGEMENT_IMAGE:?missing isolated image}",
                "build": {"context": ".", "dockerfile": "backend/Dockerfile"},
                "command": ["python", "-m", "django_core_micha.test_management.isolated_entrypoint"],
                "env_file": [".env"],
                "environment": {
                    "DJANGO_SETTINGS_MODULE": "backend.local_test_management_settings",
                    "ENV_TYPE": "${ENV_TYPE:?missing original environment identity}",
                    "DB_HOST": "db", "DB_PORT": "5432", "DB_NAME": "${TEST_MANAGEMENT_DATABASE:?missing isolated database name}",
                    "DB_USER": "test_management", "DB_PASSWORD": "${TEST_MANAGEMENT_DATABASE_PASSWORD:?missing isolated database password}",
                    "DJANGO_SECRET_KEY": "${TEST_MANAGEMENT_DJANGO_SECRET:?missing isolated application secret}",
                    "TEST_MANAGEMENT_INSTANCE_ID": "${TEST_MANAGEMENT_INSTANCE_ID:?missing instance ID}",
                    "TEST_MANAGEMENT_CONTROLLER_TOKEN": "${TEST_MANAGEMENT_CONTROLLER_TOKEN:?missing controller token}",
                    "FRONTEND_BUILD_DIR": "/app/frontend_host/build_current",
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                },
                "volumes": ["./backend:/app/backend:ro", "./frontend/build_current:/app/frontend_host/build_current:ro",
                            {"type": "volume", "source": "media", "target": "/app/backend/media", "volume": {"nocopy": True}}],
                "depends_on": {"db": {"condition": "service_healthy"}},
                "networks": ["isolated"],
            },
            "db": {
                "image": database_image,
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
    })

@pytest.mark.parametrize("args", [None, [], "x", {"LOWERcase": "0"}, {"API_TOKEN": "x"}, {"MONKEY": "x"}, {"AUTH_MODE": "x"}, {"TEST_MANAGEMENT_X": "x"}, {"DOCKER_X": "x"}, {"COMPOSE_X": "x"}, {"HTTP_PROXY": "x"}, {"https_proxy": "x"}, {"OK": "${VALUE}"}, {"OK": ""}, {"OK": 1}, {"OK": "x" * 41}, {f"ARG{i}": "x" for i in range(9)}])
def test_build_args_bad_declaration_before_docker(tmp_path, monkeypatch, args):
    root = app(tmp_path, "app", "alpha")
    (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": args}))
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *a: pytest.fail("Docker contacted"))
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)  # declaration loading itself rejects, independent of any manifest
    with pytest.raises(starter.IsolationError):
        starter.start_isolated_instance(root, {"ENV_TYPE": "local"})


def test_build_args_duplicate_and_forbidden_consumers(tmp_path):
    root = app(tmp_path, "app", "alpha")
    declaration = root / "test-management.consumer.json"
    for raw in ('{"version":1,"identity":"alpha","build_args":{"X":"1","X":"2"}}',
                '{"version":1,"identity":"alpha","mode":"synthetic-local","build_args":{}}',
                '{"version":1,"identity":"alpha","build_args":{},"extra":1}'):
        declaration.write_text(raw)
        with pytest.raises(starter.IsolationError):
            starter.consumer(root)
    legacy = app(tmp_path, "innoservice", "innoservice")
    (legacy / "test-management.consumer.json").write_text('{"version":1,"identity":"innoservice","build_args":{}}')
    with pytest.raises(starter.IsolationError):
        starter.consumer(legacy)


def test_build_args_manifest_and_image_contract(tmp_path):
    root = app(tmp_path, "app", "alpha")
    declaration = root / "test-management.consumer.json"
    manifest = root / "docker-compose.test-management.yml"
    config = starter.yaml.safe_load(_manifest_text("postgres:18"))
    old_image = starter.consumer_image("alpha", root)
    manifest.write_text(starter.yaml.safe_dump(config))
    starter.validate_compose_file(manifest, identity="alpha")
    args = {"INSTALL_ENGINE": "0", "OPTION": "a-b.1"}
    declaration.write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": args}))
    image = starter.consumer_image("alpha", root)
    assert image != old_image and not starter._image_matches(old_image, "alpha", root)
    declaration.write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": dict(reversed(list(args.items())))}))
    assert starter.consumer_image("alpha", root) == image
    build = config["services"]["backend"]["build"]
    for bad in (None, {"INSTALL_ENGINE": "0"}, {**args, "EXTRA": "1"}, {**args, "INSTALL_ENGINE": 0}, list(args)):
        if bad is None:
            build.pop("args", None)
        else:
            build["args"] = bad
        manifest.write_text(starter.yaml.safe_dump(config))
        with pytest.raises(starter.IsolationError):
            starter.validate_compose_file(manifest, identity="alpha")
    build["args"] = args
    manifest.write_text(starter.yaml.safe_dump(config))
    starter.validate_compose_file(manifest, identity="alpha")
    assert starter._image_matches(image, "alpha", root)
    declaration.write_text(json.dumps({"version": 1, "identity": "alpha"}))
    assert starter.consumer_image("alpha", root) == old_image
    with pytest.raises(starter.IsolationError):
        starter.validate_compose_file(manifest, identity="alpha")


def test_build_args_start_uses_literal_manifest(tmp_path, monkeypatch):
    root = app(tmp_path, "app", "alpha")
    args = {"INSTALL_ENGINE": "0"}
    (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": args}))
    config = starter.yaml.safe_load(_manifest_text("postgres:18"))
    config["services"]["backend"]["build"]["args"] = args
    (root / "docker-compose.test-management.yml").write_text(starter.yaml.safe_dump(config))
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *a: None)
    monkeypatch.setattr(starter.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stderr="no such volume"))
    calls = []
    monkeypatch.setattr(starter, "_run_private", lambda command, **kw: calls.append((command, kw)) or 0)
    monkeypatch.setattr(starter, "_finish_start", lambda *a, **kw: {})
    starter.start_isolated_instance(root, {"ENV_TYPE": "local", "BUILD_SECRET_TOKEN": "secret", "INSTALL_ENGINE": "1"})
    command, kwargs = calls[0]
    assert command[-3:] == ["up", "-d", "--build"] and "--build-arg" not in command
    assert starter.yaml.safe_load((root / "docker-compose.test-management.yml").read_text())["services"]["backend"]["build"]["args"] == args
    assert "secret" not in command
    # Only the manifest's literal args reach the build; the arg-specific image name is what Compose receives.
    assert kwargs["env"]["TEST_MANAGEMENT_IMAGE"] == starter.consumer_image("alpha", root)

@pytest.mark.parametrize("changed", [{"INSTALL_ENGINE": "1"}, {"OTHER_NAME": "0"}, {"INSTALL_ENGINE": "0", "EXTRA": "1"}])
def test_build_args_change_the_image_name(tmp_path, changed):
    root = app(tmp_path, "app", "alpha")
    declaration = root / "test-management.consumer.json"
    names = set()
    for args in ({"INSTALL_ENGINE": "0"}, changed):
        declaration.write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": args}))
        names.add(starter.consumer_image("alpha", root))
    assert len(names) == 2
    declaration.write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": {"INSTALL_ENGINE": "0"}}))
    assert not starter._image_matches(next(iter(names - {starter.consumer_image("alpha", root)})), "alpha", root)


def test_stop_with_build_args_checks_the_manifest_before_docker(tmp_path, monkeypatch):
    root = app(tmp_path, "app", "alpha")
    args = {"INSTALL_ENGINE": "0"}
    (root / "test-management.consumer.json").write_text(json.dumps({"version": 1, "identity": "alpha", "build_args": args}))
    (root / "frontend/build_current/index.html").unlink()
    config = starter.yaml.safe_load(_manifest_text("postgres:18"))
    config["services"]["backend"]["build"]["args"] = args
    manifest = root / "docker-compose.test-management.yml"
    manifest.write_text(starter.yaml.safe_dump(config))
    project = "alpha-tm-" + "1" * 32
    calls = []
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *_: calls.append("docker"))
    monkeypatch.setattr(starter, "validate_runtime", lambda *a, **kw: calls.append("verified"))
    monkeypatch.setattr(starter, "_run_private", lambda command, **kw: calls.append(command) or 0)
    starter.stop_isolated_instance(root, project, {})
    assert calls[:2] == ["docker", "verified"]
    calls.clear()
    config["services"]["backend"]["build"]["args"] = {"INSTALL_ENGINE": "1"}
    manifest.write_text(starter.yaml.safe_dump(config))
    with pytest.raises(starter.IsolationError):
        starter.stop_isolated_instance(root, project, {})
    assert calls == []


def test_build_args_declaration_does_not_make_a_non_declaring_app_eligible(tmp_path):
    root = app(tmp_path, "other-app", None)
    config = starter.yaml.safe_load(_manifest_text("postgres:18"))
    config["services"]["backend"]["build"]["args"] = {"INSTALL_ENGINE": "0"}
    (root / "docker-compose.test-management.yml").write_text(starter.yaml.safe_dump(config))
    with pytest.raises(starter.IsolationError):
        starter.consumer(root)
    with pytest.raises(starter.IsolationError):
        starter.consumer_image("other", root)
