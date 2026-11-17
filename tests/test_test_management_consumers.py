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
    def runtime(*args, **kwargs):
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


@pytest.mark.parametrize("identity", ["alpha", "beta"])
def test_runtime_and_stopped_instance_verify_actual_resources(tmp_path, identity):
    root = app(tmp_path, identity, identity)
    project = identity + "-tm-" + "1" * 32
    backend, db, network = project + "-backend-1", project + "-db-1", project + "_isolated"
    values = {}
    for name, service, image in ((backend, "backend", starter.consumer_image(identity, root)), (db, "db", "postgres:18")):
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
    original_source = values[backend, ".Mounts"][0]["Source"]
    values[backend, ".Mounts"][0]["Source"] = str(tmp_path / "foreign/backend")
    with pytest.raises(starter.IsolationError):
        starter.validate_runtime(project, inspect, mountinfo, identity=identity, cwd=root)
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
