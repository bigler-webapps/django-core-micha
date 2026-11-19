import json
import io
import re
import subprocess
from types import SimpleNamespace

import pytest

from django_core_micha.scripts.isolated_test_start import IsolationError
from django_core_micha.test_management import local_proxy


def test_snapshot_inspects_only_named_nonsecret_fields_and_is_fresh(monkeypatch):
    calls = []
    generation = 1
    allowed = [
        [".Config.Labels", ".Config.Image", ".State.Running", ".HostConfig.Privileged", ".NetworkSettings.Networks", ".Id", ".Mounts", ".NetworkSettings.Ports", ".HostConfig.PortBindings"],
        [".Internal", ".Labels", ".Containers"],
        [".Labels", ".Driver", ".Scope", ".Options"],
    ]

    def run(command, **kwargs):
        assert command[:3] == ["docker", "inspect", "--format"]
        assert kwargs["check"] is True and kwargs["timeout"] == 15
        assert kwargs["stderr"] == subprocess.DEVNULL and kwargs["shell"] is False
        fields = re.findall(r"\{\{json ([^}]+)\}\}", command[3])
        assert fields == allowed[len(calls) % 3]
        calls.append(command)
        return SimpleNamespace(stdout="\n".join(json.dumps([f"{generation}:{name}:{field}" for field in fields]) for name in command[4:]))

    monkeypatch.setattr(local_proxy.subprocess, "run", run)
    project = "alpha-tm-" + "1" * 32
    first = local_proxy._runtime_snapshot(project)
    generation = 2
    second = local_proxy._runtime_snapshot(project)
    assert first(project + "-backend-1", ".State.Running") == f"1:{project}-backend-1:.State.Running"
    assert second(project + "-db-1", ".State.Running") == f"2:{project}-db-1:.State.Running"
    assert second(project + "_isolated", ".Internal") == f"2:{project}_isolated:.Internal"
    assert second(project + "_media", ".Options") == f"2:{project}_media:.Options"
    assert len(calls) == 6
    with pytest.raises(IsolationError):
        first(project + "-backend-1", ".Config.Env")
    with pytest.raises(IsolationError):
        first("foreign", ".Id")


@pytest.mark.parametrize("output", ["", "[]", "not-json\n[]", "{}\n[]", "[1]\n[1]", "[]\n[]\n[]"])
def test_incomplete_or_invalid_snapshot_fails_closed(monkeypatch, output):
    monkeypatch.setattr(local_proxy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))
    with pytest.raises(IsolationError):
        local_proxy._runtime_snapshot("alpha-tm-" + "1" * 32)


def test_partial_snapshot_fails_closed(monkeypatch):
    monkeypatch.setattr(local_proxy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps([None] * 9)))
    with pytest.raises(IsolationError, match="Incomplete"):
        local_proxy._runtime_snapshot("alpha-tm-" + "1" * 32)


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired('docker', 15), subprocess.CalledProcessError(1, 'docker'), OSError()])
def test_failed_docker_inspection_fails_closed(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(local_proxy.subprocess, "run", fail)
    with pytest.raises(IsolationError):
        local_proxy._runtime_snapshot("alpha-tm-" + "1" * 32)


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired('docker', 15), subprocess.CalledProcessError(1, 'docker')])
def test_handler_rejects_when_docker_inspection_fails(tmp_path, monkeypatch, error):
    root = tmp_path / "app"
    root.mkdir()
    project = "alpha-tm-" + "1" * 32
    handler_class = local_proxy.handler_for(project, root)
    handler = handler_class.__new__(handler_class)
    handler.path, handler.command, handler.headers = "/api/healthz", "GET", {}
    handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
    results = []
    handler.send_error = lambda status: results.append(status)
    handler.send_response = lambda status: results.append(status)
    monkeypatch.setattr(local_proxy, "consumer", lambda cwd: (root, "alpha"))
    monkeypatch.setattr(local_proxy, "validate_compose_file", lambda *args, **kwargs: None)
    commands = []
    def fail(command, **kwargs):
        commands.append(command)
        assert command[1] == "inspect"
        raise error
    monkeypatch.setattr(local_proxy.subprocess, "run", fail)
    handler.handle_app()
    assert results == [502]
    assert all(command[1] != "compose" for command in commands)


def test_handler_rechecks_snapshot_before_each_request(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    project = "alpha-tm-" + "1" * 32
    handler_class = local_proxy.handler_for(project, root)
    handler = handler_class.__new__(handler_class)
    handler.path, handler.command, handler.headers = "/api/healthz", "GET", {}
    handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
    results, generation, checks, inspect_calls = [], 0, [], []
    handler.send_error = lambda status: results.append(status)
    handler.send_response = lambda status: results.append(status)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    monkeypatch.setattr(local_proxy, "consumer", lambda cwd: (root, "alpha"))
    monkeypatch.setattr(local_proxy, "validate_compose_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(local_proxy, "compose_env", lambda *args: {})
    def verify(*args, inspect, **kwargs):
        current = inspect(project + "-backend-1", ".State.Running")
        checks.append(current)
        if not current:
            raise IsolationError("runtime changed")
    monkeypatch.setattr(local_proxy, "validate_runtime", verify)
    def run(command, **kwargs):
        nonlocal generation
        if command[1] == "inspect":
            inspect_calls.append(command)
            fields = re.findall(r"\{\{json ([^}]+)\}\}", command[3])
            if ".State.Running" in fields:
                generation += 1
            return SimpleNamespace(stdout="\n".join(json.dumps([generation == 1 for _ in fields]) for _ in command[4:]))
        return SimpleNamespace(stdout=json.dumps({"status": 200, "headers": [], "body": "b2s="}))
    monkeypatch.setattr(local_proxy.subprocess, "run", run)
    handler.handle_app()
    handler.handle_app()
    assert checks == [True, False]
    assert results == [200, 502]
    assert len(inspect_calls) == 6
