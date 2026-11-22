"""Opt-in real Docker proof; only run with approval for the named disposal."""

from pathlib import Path
import json
import re
import subprocess
from uuid import UUID

import pytest

from django_core_micha.scripts import isolated_test_start as launcher
from django_core_micha.scripts.synthetic_test_fixture import create_fixture


def _docker(*arguments, check=True):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True,
                          timeout=60, check=check)


def _db(project, instance, sql):
    return _docker("exec", project + "-db-1", "psql", "-X", "-A", "-t", "-U",
                   "test_management", "-d", "dcm_tm_" + instance, "-c", sql).stdout.strip()


def _media(project, code):
    return _docker("exec", project + "-backend-1", "python", "-c", code).stdout.strip()


def test_real_disposal_preserves_other_instance(tmp_path, monkeypatch, request):
    handle_file = request.config.getoption("--dcm-disposal-proof-handles")
    if handle_file is None:
        pytest.skip("real Docker disposal proof requires explicit approved handles")
    handles = json.loads(Path(handle_file).read_text(encoding="utf-8"))
    if (not isinstance(handles, list) or len(handles) != 2 or handles[0] == handles[1] or
            any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) for value in handles)):
        raise ValueError("Proof requires two distinct UUID hex handles")
    first_id, second_id = handles
    root = create_fixture(parent=tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    identifiers = iter([UUID(hex=first_id), UUID(hex=second_id)])
    monkeypatch.setattr(launcher.uuid, "uuid4", lambda: next(identifiers))
    projects = ["dcm-synthetic-tm-" + first_id, "dcm-synthetic-tm-" + second_id]
    for project in projects:
        for suffix in ("_database", "_media"):
            assert _docker("volume", "inspect", project + suffix, check=False).returncode != 0
    first = launcher.start_isolated_instance(root)
    second = launcher.start_isolated_instance(root)
    assert [first["project"], second["project"]] == projects
    for project, instance in zip(projects, (first_id, second_id)):
        _db(project, instance, "CREATE TABLE disposal_marker (value text); INSERT INTO disposal_marker VALUES ('" + instance + "');")
        _media(project, "from pathlib import Path; Path('/app/backend/media/marker').write_text('" + instance + "')")
        launcher.stop_isolated_instance(root, project)
        launcher.resume_isolated_instance(root, project)
        assert _db(project, instance, "SELECT value FROM disposal_marker;") == instance
        assert _media(project, "from pathlib import Path; print(Path('/app/backend/media/marker').read_text())") == instance
    launcher.dispose_isolated_instance(root, projects[0])
    for suffix in ("-backend-1", "-db-1"):
        assert _docker("container", "inspect", projects[0] + suffix, check=False).returncode != 0
    for suffix in ("_database", "_media"):
        assert _docker("volume", "inspect", projects[0] + suffix, check=False).returncode != 0
    assert _db(projects[1], second_id, "SELECT value FROM disposal_marker;") == second_id
    assert _media(projects[1], "from pathlib import Path; print(Path('/app/backend/media/marker').read_text())") == second_id
    assert (root / "backend" / "Dockerfile").is_file()
    assert (root / "frontend" / "build_current" / "index.html").is_file()
    try:
        launcher.resume_isolated_instance(root, projects[0])
    except launcher.IsolationError:
        pass
    else:
        raise AssertionError("Disposed instance resumed")
    launcher.stop_isolated_instance(root, projects[1])
