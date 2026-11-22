"""Contract for terminal disposal, using only launcher Docker test doubles.

Proposed public seam: dispose_isolated_instance(cwd, project, inherited_env),
parallel to stop/resume. It raises IsolationError for an incomplete or unsafe
operation. The fake models Docker CLI removal, not persistent-data integration.
"""

import json
from copy import deepcopy
import subprocess
import sys
from pathlib import Path

import pytest

from django_core_micha.scripts import isolated_test_start as starter
from django_core_micha.scripts import test_management_lock as lifecycle_lock
from django_core_micha.scripts import test_management_disposal as disposal


FIRST = "alpha-tm-" + "1" * 32
SECOND = "alpha-tm-" + "2" * 32
ENV = {"ENV_TYPE": "local"}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle_lock, "_lock_directory", lambda: tmp_path / "locks")
    monkeypatch.setattr(disposal, "_lock_directory", lambda: tmp_path / "locks")
    root = tmp_path / "checkout"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "source.py").write_text("source", encoding="utf-8")
    (root / "frontend" / "build_current").mkdir(parents=True)
    (root / "frontend" / "build_current" / "index.html").write_text("build", encoding="utf-8")
    (root / "docker-compose.test-management.yml").write_text("services: {}", encoding="utf-8")
    (root / "test-management.consumer.json").write_text(
        json.dumps({"version": 1, "identity": "alpha"}), encoding="utf-8"
    )
    values, markers, commands = {}, {}, []
    for project in (FIRST, SECOND):
        backend, db, network = project + "-backend-1", project + "-db-1", project + "_isolated"
        for name, service, image in ((backend, "backend", starter.consumer_image("alpha", root)),
                                     (db, "db", "postgres:18")):
            values[name] = {
                ".Config.Labels": {"com.docker.compose.project": project,
                                   "com.docker.compose.service": service,
                                   "com.docker.compose.project.working_dir": str(root)},
                ".Config.Image": image, ".State.Running": True,
                ".HostConfig.Privileged": False, ".HostConfig.PortBindings": {},
                ".NetworkSettings.Networks": {network: {"IPAddress": "10.1.2.3"}},
                ".NetworkSettings.Ports": {}, ".Id": project + service,
            }
        values[backend][".Mounts"] = [
            {"Destination": "/app/backend", "Source": str(root / "backend"), "Type": "bind", "RW": False},
            {"Destination": "/app/frontend_host/build_current", "Source": str(root / "frontend/build_current"), "Type": "bind", "RW": False},
            {"Destination": "/app/backend/media", "Name": project + "_media", "Type": "volume", "RW": True},
        ]
        values[db][".Mounts"] = [{"Destination": "/var/lib/postgresql", "Name": project + "_database", "Type": "volume", "RW": True}]
        values[network] = {".Internal": True,
                           ".Id": network + "-id",
                           ".Labels": {"com.docker.compose.project": project, "com.docker.compose.network": "isolated"},
                           ".Containers": {project + "backend": {"IPv4Address": "10.1.2.4/24"},
                                           project + "db": {"IPv4Address": "10.1.2.3/24"}}}
        for suffix in ("database", "media"):
            name = project + "_" + suffix
            values[name] = {".Labels": {"com.docker.compose.project": project, "com.docker.compose.volume": suffix},
                            ".Driver": "local", ".Scope": "local", ".Options": None, ".CreatedAt": "2026-01-01T00:00:00Z"}
            markers[name] = project + ":" + suffix

    state = {"interrupt": False, "during_removal": None}

    def inspect(name, expression, **kwargs):
        try:
            return deepcopy(values[name][expression])
        except KeyError as exc:
            raise starter.IsolationError("Resource cannot be inspected") from exc

    def run(command, **kwargs):
        commands.append(list(command))
        # Fail closed on unmodelled Docker operations, rather than invoking Docker.
        if command[:2] == ["docker", "stop"]:
            for name, fields in values.items():
                if fields.get(".Id") in command[2:] and ".State.Running" in fields:
                    fields[".State.Running"] = False
            return 0
        if command[:2] == ["docker", "rm"]:
            names = [name for name, fields in values.items() if fields.get(".Id") in command[2:]]
            for name in names:
                values.pop(name, None)
            for fields in values.values():
                if ".Containers" in fields:
                    fields[".Containers"] = {key: val for key, val in fields[".Containers"].items() if key not in command[2:]}
            if state["during_removal"] is not None:
                callback, state["during_removal"] = state["during_removal"], None
                callback()
            if state["interrupt"]:
                state["interrupt"] = False
                raise starter.IsolationError("Interrupted after containers disappeared")
            return 0
        if command[:3] == ["docker", "volume", "rm"]:
            for name in command[3:]:
                values.pop(name, None)
                markers.pop(name, None)
            return 0
        if command[:3] == ["docker", "network", "rm"]:
            for name in [name for name, fields in values.items() if fields.get(".Id") in command[3:]]:
                values.pop(name, None)
            return 0
        pytest.fail(f"Unexpected simulated command: {command!r}")

    monkeypatch.setattr(starter, "_docker_json", inspect)
    monkeypatch.setattr(starter, "_mountinfo", lambda *a, **kw: "1 2 3:4 / /app/backend/media rw - ext4 /dev/test rw\n1 2 3:4 / /var/lib/postgresql rw - ext4 /dev/test rw")
    monkeypatch.setattr(starter, "_assert_local_docker", lambda *a, **kw: None)
    monkeypatch.setattr(starter, "validate_compose_file", lambda *a, **kw: None)
    monkeypatch.setattr(starter, "_run_private", run)
    def output(command, **kwargs):
        if command[:2] == ["docker", "info"]:
            return "local-daemon"
        if command[:3] == ["docker", "ps", "-aq"]:
            volume = command[-1].removeprefix("volume=")
            return "\n".join(fields[".Id"] for fields in values.values()
                             if any(mount.get("Name") == volume for mount in fields.get(".Mounts", [])))
        pytest.fail(f"Unexpected simulated query: {command!r}")
    monkeypatch.setattr(starter, "_docker_output", output)
    monkeypatch.setattr(starter.subprocess, "run", lambda *a, **kw: pytest.fail("Unmodelled subprocess; real Docker is forbidden"))
    return root, values, markers, commands, state


def test_interrupted_disposal_refuses_retry_when_volume_ownership_becomes_uncertain(runtime):
    root, values, markers, commands, state = runtime
    state["interrupt"] = True
    with pytest.raises(starter.IsolationError):
        starter.dispose_isolated_instance(root, FIRST, ENV)
    assert FIRST + "-backend-1" not in values and FIRST + "-db-1" not in values
    assert FIRST + "_database" in markers and FIRST + "_media" in markers
    values[FIRST + "_media"][".Labels"] = {}
    before_values, before_markers, before_commands = deepcopy(values), dict(markers), list(commands)
    with pytest.raises(starter.IsolationError):
        starter.dispose_isolated_instance(root, FIRST, ENV)
    assert values == before_values and markers == before_markers and commands == before_commands
    assert markers[SECOND + "_database"] == SECOND + ":database"
    assert markers[SECOND + "_media"] == SECOND + ":media"
    with pytest.raises(starter.IsolationError):
        starter.resume_isolated_instance(root, FIRST, ENV)
    with pytest.raises(starter.IsolationError):
        starter.stop_isolated_instance(root, FIRST, ENV)


def test_competing_resume_and_dispose_are_refused_during_disposal(runtime):
    root, values, markers, commands, state = runtime
    second_before = {name: deepcopy(value) for name, value in values.items() if name.startswith(SECOND)}
    second_markers = {name: value for name, value in markers.items() if name.startswith(SECOND)}
    attempted = []

    def compete():
        for operation in (starter.resume_isolated_instance, starter.dispose_isolated_instance):
            with pytest.raises(starter.IsolationError):
                operation(root, FIRST, ENV)
            attempted.append(operation.__name__)

    state["during_removal"] = compete
    starter.dispose_isolated_instance(root, FIRST, ENV)
    assert attempted == ["resume_isolated_instance", "dispose_isolated_instance"]
    assert not any(name.startswith(FIRST) for name in values)
    assert not any(name.startswith(FIRST) for name in markers)
    assert {name: value for name, value in values.items() if name.startswith(SECOND)} == second_before
    assert {name: value for name, value in markers.items() if name.startswith(SECOND)} == second_markers
    assert (root / "backend" / "source.py").read_text(encoding="utf-8") == "source"
    assert (root / "frontend" / "build_current" / "index.html").read_text(encoding="utf-8") == "build"
    with pytest.raises(starter.IsolationError):
        starter.resume_isolated_instance(root, FIRST, ENV)
    before = list(commands)
    starter.dispose_isolated_instance(root, FIRST, ENV)
    assert commands == before


@pytest.mark.parametrize("mutation", ["foreign_checkout", "unexpected_mount", "wrong_volume_owner", "foreign_network_member", "shared_volume", "nonlocal_environment"])
def test_ambiguous_preflight_removes_nothing(runtime, monkeypatch, tmp_path, mutation):
    root, values, markers, commands, _ = runtime
    if mutation == "foreign_checkout":
        values[FIRST + "-backend-1"][".Config.Labels"]["com.docker.compose.project.working_dir"] = str(tmp_path / "foreign")
    elif mutation == "unexpected_mount":
        values[FIRST + "-backend-1"][".Mounts"].append({"Destination": "/unexpected", "Type": "bind", "Source": str(tmp_path), "RW": True})
    elif mutation == "wrong_volume_owner":
        values[FIRST + "_media"][".Labels"]["com.docker.compose.project"] = SECOND
    elif mutation == "foreign_network_member":
        values[FIRST + "_isolated"][".Containers"]["foreign"] = {"IPv4Address": "10.1.2.9/24"}
    elif mutation == "shared_volume":
        values[SECOND + "-backend-1"][".Mounts"].append({"Name": FIRST + "_media"})
    before_values, before_markers = deepcopy(values), dict(markers)
    with pytest.raises(starter.IsolationError):
        starter.dispose_isolated_instance(root, FIRST, {"ENV_TYPE": "production"} if mutation == "nonlocal_environment" else ENV)
    assert values == before_values and markers == before_markers and commands == []


def test_stopped_instance_can_be_disposed(runtime):
    root, values, markers, commands, _ = runtime
    for service in ("backend", "db"):
        values[FIRST + "-" + service + "-1"][".State.Running"] = False
    values[FIRST + "_isolated"][".Containers"] = {}
    launcher_result = starter.dispose_isolated_instance(root, FIRST, ENV)
    assert launcher_result is None
    assert not any(name.startswith(FIRST) for name in values)
    assert not any(name.startswith(FIRST) for name in markers)
    assert all(name.startswith(SECOND) for name in markers)
    assert not any(command[:2] == ["docker", "stop"] for command in commands)


def test_dispose_holds_lock_during_preflight_before_marker(runtime, monkeypatch):
    root, values, markers, commands, _ = runtime
    original_output = starter._docker_output
    attempts = []

    def output(command, **kwargs):
        if command[:3] == ["docker", "ps", "-aq"] and not attempts:
            attempts.append("started")
            assert not disposal._journal_path(FIRST).exists()
            for operation in (starter.resume_isolated_instance, starter.stop_isolated_instance, starter.dispose_isolated_instance):
                with pytest.raises(starter.IsolationError, match="busy"):
                    operation(root, FIRST, ENV)
                attempts.append(operation.__name__)
        return original_output(command, **kwargs)

    monkeypatch.setattr(starter, "_docker_output", output)
    starter.dispose_isolated_instance(root, FIRST, ENV)
    assert attempts == ["started", "resume_isolated_instance", "stop_isolated_instance", "dispose_isolated_instance"]


def test_interruption_before_first_removal_blocks_stop_and_resume(runtime, monkeypatch):
    root, values, markers, commands, _ = runtime
    original_run = starter._run_private

    def interrupt(command, **kwargs):
        if command[:2] == ["docker", "stop"]:
            raise starter.IsolationError("simulated interruption")
        return original_run(command, **kwargs)

    monkeypatch.setattr(starter, "_run_private", interrupt)
    with pytest.raises(starter.IsolationError, match="interruption"):
        starter.dispose_isolated_instance(root, FIRST, ENV)
    before = deepcopy(values), dict(markers), list(commands)
    for operation in (starter.resume_isolated_instance, starter.stop_isolated_instance):
        with pytest.raises(starter.IsolationError, match="Disposal was attempted"):
            operation(root, FIRST, ENV)
    assert (values, markers, commands) == before


@pytest.mark.parametrize("change", ["volume", "network", "daemon"])
def test_changed_ownership_after_removal_preserves_remaining_resources(runtime, monkeypatch, change):
    root, values, markers, commands, state = runtime
    original_output = starter._docker_output
    daemon_changed = False

    def change_after_removal():
        nonlocal daemon_changed
        if change == "volume":
            values[FIRST + "_database"][".Labels"] = {}
        elif change == "network":
            values[FIRST + "_isolated"][".Containers"]["foreign"] = {}
        else:
            daemon_changed = True

    def output(command, **kwargs):
        if daemon_changed and command[:2] == ["docker", "info"]:
            return "different-daemon"
        return original_output(command, **kwargs)

    monkeypatch.setattr(starter, "_docker_output", output)
    state["during_removal"] = change_after_removal
    with pytest.raises(starter.IsolationError):
        starter.dispose_isolated_instance(root, FIRST, ENV)
    assert FIRST + "_isolated" in values
    if change in {"volume", "daemon"}:
        assert FIRST + "_database" in markers and FIRST + "_media" in markers
    assert markers[SECOND + "_database"] == SECOND + ":database"
    assert markers[SECOND + "_media"] == SECOND + ":media"


def test_stop_cannot_interleave_with_resume_before_start_finishes(runtime, monkeypatch):
    root, values, markers, commands, state = runtime
    del values, markers, commands, state
    monkeypatch.setattr(starter, "validate_stopped_instance", lambda *a, **kw: None)
    monkeypatch.setattr(starter, "validate_runtime", lambda *a, **kw: (None, "10.1.2.3"))
    monkeypatch.setattr(starter, "_docker_json", lambda *a, **kw: "healthy")
    attempted = []

    def run(command, **kwargs):
        if command[-2:] == ["start", "db"]:
            with pytest.raises(starter.IsolationError):
                starter.stop_isolated_instance(root, FIRST, ENV)
            attempted.append("stop refused")
            return 0
        if command[:2] == ["docker", "stop"]:
            attempted.append("stop ran during resume")
            return 0
        return 0

    monkeypatch.setattr(starter, "_run_private", run)
    monkeypatch.setattr(starter, "_finish_start", lambda *a, **kw: {"project": FIRST})
    starter.resume_isolated_instance(root, FIRST, ENV)
    assert attempted == ["stop refused"]


def test_instance_lock_coordinates_separate_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle_lock, "_lock_directory", lambda: tmp_path)
    code = "\n".join([
        "import sys",
        "from pathlib import Path",
        "sys.path.insert(0, sys.argv[1])",
        "from django_core_micha.scripts import test_management_lock as lock",
        "lock._lock_directory = lambda: Path(sys.argv[2])",
        "try:",
        "    with lock.instance_lock(sys.argv[3]): pass",
        "except lock.LifecycleBusyError:",
        "    sys.exit(7)",
    ])
    src = str(Path(__file__).resolve().parents[1] / "src")
    def probe(project):
        return subprocess.run([sys.executable, "-c", code, src, str(tmp_path), project],
                              capture_output=True, timeout=10, check=False).returncode
    with lifecycle_lock.instance_lock(FIRST):
        assert probe(FIRST) == 7
        assert probe(SECOND) == 0
    assert probe(FIRST) == 0
