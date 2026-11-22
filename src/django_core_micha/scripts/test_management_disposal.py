"""Fail-closed disposal of a verified, dedicated local test instance."""

import json
import os
from pathlib import Path

from .test_management_lock import _lock_directory


def _error(message):
    from .isolated_test_start import IsolationError
    return IsolationError(message)


def _journal_path(project):
    import hashlib
    return _lock_directory() / (hashlib.sha256(project.encode("utf-8")).hexdigest() + ".disposal.json")


def ensure_not_disposing(project):
    journal = _journal_path(project)
    if journal.exists() or journal.is_symlink():
        raise _error("Disposal was attempted for this instance; stop and resume are unavailable.")


def _write_journal(path, record):
    """Create a durable refusal marker before the first irreversible action."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise _error("Disposal was already attempted; the instance may be partially disposed. Inspect its recorded state before taking any action.") from exc
    except OSError as exc:
        raise _error("Unable to record disposal safely; no removal was attempted.") from exc


def dispose(cwd, project, env, *, identity):
    from . import isolated_test_start as starter

    inspect = lambda name, expression: starter._docker_json(name, expression, env=env)
    output = lambda command: starter._docker_output(command, cwd=cwd, env=env)
    backend, db = project + "-backend-1", project + "-db-1"
    network = project + "_isolated"
    volumes = [project + "_database", project + "_media"]
    journal = _journal_path(project)
    if journal.exists() or journal.is_symlink():
        completed = journal.with_suffix(".completed.json")
        try:
            if completed.is_symlink() or journal.is_symlink():
                raise ValueError
            record = json.loads(journal.read_text(encoding="utf-8"))
            result = json.loads(completed.read_text(encoding="utf-8"))
            if (record.get("project") == project and record.get("checkout") == str(Path(cwd).resolve()) and
                    result == {"project": project, "state": "disposed"}):
                print(f"[INFO] Instance was already disposed: {project}", flush=True)
                return
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        raise _error("Disposal was already attempted; the instance may be partially disposed. Inspect its recorded state before taking any action.")

    try:
        states = [inspect(name, ".State.Running") for name in (backend, db)]
        if states == [True, True]:
            starter.validate_runtime(project, inspect=inspect,
                mountinfo=lambda name: starter._mountinfo(name, env=env), identity=identity, cwd=cwd)
        elif states == [False, False]:
            starter.validate_stopped_instance(project, inspect=inspect, identity=identity, cwd=cwd)
        else:
            raise _error("The instance has inconsistent container state; no removal was attempted.")
        ids = [inspect(name, ".Id") for name in (backend, db)]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != 2:
            raise ValueError
        labels = inspect(network, ".Labels")
        network_id = inspect(network, ".Id")
        members = inspect(network, ".Containers") or {}
        if (inspect(network, ".Internal") is not True or
                labels.get("com.docker.compose.project") != project or
                labels.get("com.docker.compose.network") != "isolated" or
                not isinstance(network_id, str) or not network_id or
                not set(members).issubset(set(ids))):
            raise ValueError
        for name in (backend, db):
            if set(inspect(name, ".NetworkSettings.Networks") or {}) != {network}:
                raise ValueError
            if any((inspect(name, ".NetworkSettings.Ports") or {}).values()) or any((inspect(name, ".HostConfig.PortBindings") or {}).values()):
                raise ValueError
        snapshots = {}
        for volume, expected in zip(volumes, ids[::-1]):
            users = set(output(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + volume]).splitlines())
            if users != {expected}:
                raise ValueError
            created = inspect(volume, ".CreatedAt")
            if not isinstance(created, str) or not created:
                raise ValueError
            snapshots[volume] = {"created": created, "labels": inspect(volume, ".Labels")}
        daemon = output(["docker", "info", "--format", "{{.ID}}"])
        if not daemon:
            raise ValueError
    except starter.IsolationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _error("Resource ownership is ambiguous; no removal was attempted.") from exc

    _write_journal(journal, {"version": 1, "state": "disposal-started", "project": project,
                             "checkout": str(Path(cwd).resolve()), "daemon": daemon,
                             "containers": ids, "network": network_id, "volumes": snapshots})

    def run(command):
        if output(["docker", "info", "--format", "{{.ID}}"]) != daemon:
            raise _error("The local daemon changed during disposal; remaining resources were preserved.")
        if starter._run_private(command, cwd=cwd, env=env, timeout=60):
            raise _error("Disposal is incomplete. The recorded state requires investigation; no retry is permitted.")

    if states == [True, True]:
        run(["docker", "stop", *ids])
    run(["docker", "rm", *ids])
    for volume in volumes:
        users = output(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + volume]).splitlines()
        if (users or inspect(volume, ".CreatedAt") != snapshots[volume]["created"] or
                inspect(volume, ".Labels") != snapshots[volume]["labels"]):
            raise _error("Volume ownership changed during disposal; remaining resources were preserved.")
        run(["docker", "volume", "rm", volume])
    if inspect(network, ".Id") != network_id or (inspect(network, ".Containers") or {}):
        raise _error("Network ownership changed during disposal; remaining resources were preserved.")
    run(["docker", "network", "rm", network_id])
    _write_journal(journal.with_suffix(".completed.json"), {"project": project, "state": "disposed"})
    print(f"[INFO] Disposed isolated instance: {project}", flush=True)
