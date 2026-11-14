"""sync-secrets and the ops delivery of webapp-management (WM-SEC-30): detection, the GitHub push filter, the single delivery call."""

import io
import subprocess
import sys
from types import SimpleNamespace

import pytest

from django_core_micha.scripts import ops_delivery, sync_secrets

SECRETS_YAML = (
    "config:\n"
    "  target_repo: org/repo\n"
    "  github_environment_template: '{target}'\n"
    "  default_target: staging\n"
    "secrets:\n"
    "  RUNTIME_SECRET:\n"
    "    source: proton://Vault/Item/runtime\n"
    "  CI_ONLY:\n"
    "    source: proton://Vault/Item/ci\n"
    "    exclude_from_env: true\n"
    "  REPO_WIDE:\n"
    "    source: proton://Vault/Item/repo\n"
    "    target_scope: repo\n"
    "  NO_GITHUB:\n"
    "    source: proton://Vault/Item/nogh\n"
    "    exclude_from_github: true\n"
)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    (tmp_path / "secrets.yaml").write_text(SECRETS_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    pushes, deliveries, order = [], [], []

    def fake_run(cmd, **kwargs):
        if list(cmd[:3]) == ["gh", "secret", "set"]:
            env = cmd[cmd.index("--env") + 1] if "--env" in cmd else None
            pushes.append((cmd[3], env))
            order.append(("push", cmd[3], env))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")  # `gh api ...` environment listing: unavailable

    monkeypatch.setattr(sync_secrets.subprocess, "run", fake_run)
    monkeypatch.setattr(sync_secrets, "check_dependencies", lambda *a, **k: True)
    monkeypatch.setattr(sync_secrets, "resolve_secret_value", lambda key, *a, **k: ("VALUE-NEVER-PRINTED", "proton"))

    def fake_deliver(environments, **kwargs):
        deliveries.append(list(environments))
        order.append(("deliver", tuple(environments)))
        return 0

    monkeypatch.setattr(ops_delivery, "deliver", fake_deliver)
    return SimpleNamespace(path=tmp_path, pushes=pushes, deliveries=deliveries, order=order, monkeypatch=monkeypatch)


def managed(app, environments):
    app.monkeypatch.setattr(ops_delivery, "managed_environments", lambda *a, **k: dict(environments))


def pushed(app, env):
    return sorted(key for key, target in app.pushes if target == env)


def test_an_ops_managed_environment_gets_only_ci_only_and_repo_scoped_secrets_and_a_delivery(app, capsys):
    managed(app, {"staging": "staging"})
    sync_secrets.main(["--staging"])
    assert pushed(app, "staging") == ["CI_ONLY"]  # RUNTIME_SECRET stays out; NO_GITHUB is excluded as ever
    assert ("REPO_WIDE", None) in app.pushes  # repository-scoped: pushed as before, no environment
    assert app.deliveries == [["staging"]]
    output = capsys.readouterr().out
    assert "Skipping RUNTIME_SECRET: delivered to the ops-managed server" in output
    assert "VALUE-NEVER-PRINTED" not in output


def test_a_legacy_environment_of_the_same_app_is_pushed_exactly_as_before(app):
    managed(app, {"staging": "staging"})
    sync_secrets.main(["--production"])
    assert pushed(app, "production") == ["CI_ONLY", "RUNTIME_SECRET"]
    assert ("REPO_WIDE", None) in app.pushes
    assert app.deliveries == []


def test_one_call_for_an_app_with_one_ops_managed_and_one_legacy_environment(app):
    managed(app, {"staging": "staging"})
    sync_secrets.main([])
    assert pushed(app, "staging") == ["CI_ONLY"]
    assert pushed(app, "production") == ["CI_ONLY", "RUNTIME_SECRET"]
    assert app.deliveries == [["staging"]]


def test_staging_and_production_are_delivered_together_in_one_call_after_all_pushes(app):
    managed(app, {"staging": "staging", "production": "main-prod"})
    sync_secrets.main(["--github"])
    assert pushed(app, "staging") == ["CI_ONLY"] and pushed(app, "production") == ["CI_ONLY"]
    assert app.deliveries == [["staging", "production"]]
    assert app.order[-1] == ("deliver", ("staging", "production"))  # the delivery follows every push of the call
    assert [step for step in app.order if step[0] == "deliver"] == [app.order[-1]]


def test_a_legacy_app_never_starts_a_delivery_and_pushes_everything(app):
    managed(app, {})
    sync_secrets.main([])
    for env in ("staging", "production"):
        assert pushed(app, env) == ["CI_ONLY", "RUNTIME_SECRET"]
    assert app.deliveries == []


def test_local_never_detects_or_delivers(app):
    app.monkeypatch.setattr(ops_delivery, "managed_environments", lambda *a, **k: pytest.fail("no detection for --local"))
    with pytest.raises(SystemExit):  # no local environment in this project: the usual fatal, but never a delivery
        sync_secrets.main(["--local"])
    assert app.deliveries == [] and app.pushes == []


def test_a_failed_delivery_ends_nonzero_with_a_fixed_message_after_the_github_side_is_done(app, capsys):
    managed(app, {"staging": "staging"})
    app.monkeypatch.setattr(ops_delivery, "deliver", lambda environments, **k: 1)
    with pytest.raises(SystemExit) as exc:
        sync_secrets.main(["--staging"])
    assert exc.value.code == 1
    assert pushed(app, "staging") == ["CI_ONLY"]
    assert "the delivery to the ops-managed server(s) failed" in capsys.readouterr().out


def test_a_delivery_that_did_not_run_is_a_fixed_error(app, capsys):
    managed(app, {"staging": "staging"})

    def not_run(environments, **kwargs):
        raise ops_delivery.OpsDeliveryError("deliver_not_run")

    app.monkeypatch.setattr(ops_delivery, "deliver", not_run)
    with pytest.raises(SystemExit) as exc:
        sync_secrets.main(["--staging"])
    assert exc.value.code == 1
    assert "the delivery did not run (deliver_not_run)" in capsys.readouterr().out


def test_when_detection_fails_nothing_is_pushed_or_delivered(app, capsys):
    def broken(*args, **kwargs):
        raise ops_delivery.OpsDeliveryError("config_unreadable")

    app.monkeypatch.setattr(ops_delivery, "managed_environments", broken)
    for argv in ([], ["--staging"], ["--production"], ["--github"]):
        with pytest.raises(SystemExit) as exc:
            sync_secrets.main(argv)
        assert exc.value.code == 1
    assert app.pushes == [] and app.deliveries == []
    assert "cannot tell which environments are ops-managed (config_unreadable)" in capsys.readouterr().out


def test_an_ops_managed_environment_refuses_a_secret_source_other_than_proton(app, capsys):
    managed(app, {"staging": "staging"})
    (app.path / "values.yaml").write_text("staging: {}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        sync_secrets.main(["--staging", "--secret-source", "yaml", "--values-file", "values.yaml"])
    assert exc.value.code == 1
    assert app.pushes == [] and app.deliveries == []
    assert "delivered from Proton only" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ops_delivery itself
# ---------------------------------------------------------------------------


def cli_checkout(tmp_path, monkeypatch):
    """A fake `../webapp-management/ansible/deliver_secrets.py` beside an app directory called `demo`."""
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    cli = tmp_path / "webapp-management" / "ansible" / "deliver_secrets.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("# stub\n", encoding="utf-8")
    monkeypatch.delenv("WEBAPP_MANAGEMENT_DIR", raising=False)
    return app_dir, cli


def test_the_delivery_cli_is_found_beside_the_app_and_its_absence_means_no_managed_environment(tmp_path, monkeypatch):
    app_dir, cli = cli_checkout(tmp_path, monkeypatch)
    assert ops_delivery.cli_path(app_dir) == cli
    cli.unlink()
    assert ops_delivery.cli_path(app_dir) is None
    assert ops_delivery.managed_environments(app_dir, run=lambda *a, **k: pytest.fail("no CLI, no call")) == {}
    monkeypatch.setenv("WEBAPP_MANAGEMENT_DIR", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere" / "ansible").mkdir(parents=True)
    (tmp_path / "elsewhere" / "ansible" / "deliver_secrets.py").write_text("#\n", encoding="utf-8")
    assert ops_delivery.cli_path(app_dir) == tmp_path / "elsewhere" / "ansible" / "deliver_secrets.py"


def test_list_output_is_parsed_strictly(tmp_path, monkeypatch):
    app_dir, cli = cli_checkout(tmp_path, monkeypatch)
    calls = []

    def run_with(stdout, returncode=0):
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
        return run

    assert ops_delivery.managed_environments(app_dir, run=run_with("managed staging staging\nmanaged production main-prod\n")) == {
        "staging": "staging", "production": "main-prod"}
    assert calls[0][0] == [sys.executable, str(cli), "list", "--app", "demo"]
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert ops_delivery.managed_environments(app_dir, run=run_with("")) == {}
    for bad in ("managed qa host\n", "managed staging ho st\n", "hello\n", "managed staging staging extra\n", "managed staging $(x)\n"):
        with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
            ops_delivery.managed_environments(app_dir, run=run_with(bad))
        assert exc.value.code == "list_unreadable"
    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.managed_environments(app_dir, run=run_with("error: not_ops_managed\n", returncode=2))
    assert exc.value.code == "not_ops_managed"
    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.managed_environments(app_dir, run=run_with("garbage with a secret hunter2\n", returncode=1))
    assert exc.value.code == "list_failed" and "hunter2" not in str(exc.value)

    def explode(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.managed_environments(app_dir, run=explode)
    assert exc.value.code == "list_not_run"


def test_deliver_runs_the_cli_once_with_ordered_environments_and_prints_only_fixed_result_lines(tmp_path, monkeypatch):
    app_dir, cli = cli_checkout(tmp_path, monkeypatch)
    calls = []
    stdout = (
        "deliver demo/staging -> staging: delivered (changed=1)\n"
        "hunter2 DJANGO_SECRET_KEY=abc\n"
        "PLAY RECAP staging : ok=3\n"
        "deliver demo/production -> main-prod: FAILED (task: Write each declared credential file)\n"
        "deliver demo/production -> main-prod: FAILED (rm -rf / $(x))\n"
    )

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout=stdout, stderr="the secret stderr")

    out = io.StringIO()
    code = ops_delivery.deliver(["production", "staging", "production"], cwd=app_dir, run=run, out=out)
    assert code == 1
    assert calls == [[sys.executable, str(cli), "deliver", "--app", "demo", "--env", "staging", "--env", "production"]]
    assert out.getvalue().splitlines() == [
        "deliver demo/staging -> staging: delivered (changed=1)",
        "deliver demo/production -> main-prod: FAILED (task: Write each declared credential file)"]
    assert "hunter2" not in out.getvalue() and "secret stderr" not in out.getvalue()


def test_deliver_without_a_cli_an_environment_or_a_process_is_a_fixed_error(tmp_path, monkeypatch):
    app_dir, cli = cli_checkout(tmp_path, monkeypatch)
    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.deliver([], cwd=app_dir, run=lambda *a, **k: None)
    assert exc.value.code == "no_environment"
    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.deliver(["qa"], cwd=app_dir, run=lambda *a, **k: None)
    assert exc.value.code == "no_environment"

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.deliver(["staging"], cwd=app_dir, run=timeout)
    assert exc.value.code == "deliver_timeout"

    def missing(argv, **kwargs):
        raise FileNotFoundError("hunter2")

    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.deliver(["staging"], cwd=app_dir, run=missing)
    assert exc.value.code == "deliver_not_run" and "hunter2" not in str(exc.value)
    cli.unlink()
    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.deliver(["staging"], cwd=app_dir, run=lambda *a, **k: None)
    assert exc.value.code == "cli_missing"


# ---------------------------------------------------------------------------
# Review findings of WM-SEC-30 (regression, security, envelope lenses)
# ---------------------------------------------------------------------------


def test_server_without_a_target_uses_the_default_target_and_still_filters_and_delivers(app):
    """`--server` alone resolves its target from config.default_target; the managed check must use the RESOLVED target."""
    managed(app, {"staging": "staging"})
    sync_secrets.main(["--server"])
    assert pushed(app, "staging") == ["CI_ONLY"]
    assert app.deliveries == [["staging"]]


def test_a_later_push_failure_says_which_managed_environments_were_not_delivered(app, capsys):
    managed(app, {"staging": "staging"})
    real = sync_secrets.subprocess.run

    def fail_production(cmd, **kwargs):
        if list(cmd[:3]) == ["gh", "secret", "set"] and "--env" in cmd and cmd[cmd.index("--env") + 1] == "production":
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return real(cmd, **kwargs)

    app.monkeypatch.setattr(sync_secrets.subprocess, "run", fail_production)
    with pytest.raises(SystemExit):
        sync_secrets.main([])
    assert app.deliveries == []
    assert "Not delivered to the ops-managed server(s) staging" in capsys.readouterr().out


def test_no_webapp_management_beside_the_app_is_noted_and_means_legacy(app, capsys, monkeypatch):
    monkeypatch.delenv("WEBAPP_MANAGEMENT_DIR", raising=False)
    monkeypatch.undo()  # the fixture's patches are not needed for the detection itself
    monkeypatch.chdir(app.path)
    assert ops_delivery.cli_path() is None
    assert ops_delivery.managed_environments() == {}


def test_an_explicit_webapp_management_dir_that_holds_no_cli_is_an_error_not_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBAPP_MANAGEMENT_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(ops_delivery.OpsDeliveryError) as exc:
        ops_delivery.managed_environments(tmp_path)
    assert exc.value.code == "cli_missing"


@pytest.mark.parametrize("name", ["my app", ".hidden", "_x", "a;b", "x" * 80])
def test_an_app_directory_that_cannot_be_a_credential_entry_is_never_looked_up(tmp_path, monkeypatch, name):
    (tmp_path / "webapp-management" / "ansible").mkdir(parents=True)
    (tmp_path / "webapp-management" / "ansible" / "deliver_secrets.py").write_text("#\n", encoding="utf-8")
    app_dir = tmp_path / name
    app_dir.mkdir()
    monkeypatch.delenv("WEBAPP_MANAGEMENT_DIR", raising=False)
    assert ops_delivery.managed_environments(app_dir, run=lambda *a, **k: pytest.fail("not looked up")) == {}


def cli_stub(tmp_path, monkeypatch, name="demo"):
    cli = tmp_path / "webapp-management" / "ansible" / "deliver_secrets.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("# stub\n", encoding="utf-8")
    app_dir = tmp_path / name
    app_dir.mkdir()
    monkeypatch.delenv("WEBAPP_MANAGEMENT_DIR", raising=False)
    return app_dir, cli


def test_the_cli_error_line_and_a_silent_failure_are_always_shown(tmp_path, monkeypatch):
    app_dir, cli = cli_stub(tmp_path, monkeypatch)

    def run_with(stdout, code):
        return lambda argv, **kwargs: SimpleNamespace(returncode=code, stdout=stdout, stderr="traceback with hunter2")

    out = io.StringIO()
    assert ops_delivery.deliver(["staging"], cwd=app_dir, run=run_with("error: not_ops_managed\n", 2), out=out) == 2
    assert out.getvalue() == "error: not_ops_managed\n"
    out = io.StringIO()
    assert ops_delivery.deliver(["staging"], cwd=app_dir, run=run_with("Traceback (most recent call last):\nImportError: yaml\n", 1), out=out) == 1
    assert out.getvalue() == "error: delivery_exit_1\n"  # a crash prints one fixed line, never its text
    out = io.StringIO()
    assert ops_delivery.deliver(["staging"], cwd=app_dir, run=run_with("", 0), out=out) == 0
    assert out.getvalue() == ""
    out = io.StringIO()
    ops_delivery.deliver(["staging"], cwd=app_dir, run=run_with("error: Bad Code!\nerror: " + "x" * 60 + "\n", 2), out=out)
    assert out.getvalue() == "error: delivery_exit_2\n"


@pytest.mark.parametrize("task", ["Write each declared credential file", "Check that every selected credential can be read (before): now/then, 1.2_x-y",
                                  "a" * 80])
def test_every_failed_task_name_the_cli_lets_through_survives_the_result_line_filter(tmp_path, monkeypatch, task):
    app_dir, cli = cli_stub(tmp_path, monkeypatch)
    line = "deliver demo/staging -> staging: FAILED (task: %s)" % task
    out = io.StringIO()
    ops_delivery.deliver(["staging"], cwd=app_dir, run=lambda argv, **kw: SimpleNamespace(returncode=1, stdout=line + "\n", stderr=""), out=out)
    assert out.getvalue() == line + "\n"


def test_the_outer_timeout_exceeds_the_sum_of_the_cli_runs(tmp_path, monkeypatch):
    app_dir, cli = cli_stub(tmp_path, monkeypatch)
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ops_delivery.deliver(["staging"], cwd=app_dir, run=run)
    ops_delivery.deliver(["staging", "production"], cwd=app_dir, run=run)
    assert seen == [1500 + 120, 2 * 1500 + 120]


def test_the_real_deliver_output_and_exit_status_reach_sync_secrets_unmocked(app, tmp_path, monkeypatch, capsys):
    """The unpatched ops_delivery.deliver: a FAILED result line of the CLI is printed and sync-secrets exits non-zero."""
    managed(app, {"staging": "staging"})
    app.monkeypatch.undo()  # drop the fixture's deliver stub, keep nothing else mocked below
    monkeypatch.chdir(app.path)
    cli = tmp_path / "cli" / "ansible" / "deliver_secrets.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("#\n", encoding="utf-8")
    monkeypatch.setenv("WEBAPP_MANAGEMENT_DIR", str(tmp_path / "cli"))
    pushes = []

    def fake_run(cmd, **kwargs):
        if list(cmd[:3]) == ["gh", "secret", "set"]:
            pushes.append(cmd[3])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if len(cmd) > 2 and cmd[2] == "list":
            return SimpleNamespace(returncode=0, stdout="managed staging staging\n", stderr="")
        if len(cmd) > 2 and cmd[2] == "deliver":
            return SimpleNamespace(returncode=1, stdout="deliver %s/staging -> staging: FAILED (task: Write each declared credential file)\n" % app.path.name,
                                   stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(sync_secrets.subprocess, "run", fake_run)
    monkeypatch.setattr(sync_secrets, "check_dependencies", lambda *a, **k: True)
    monkeypatch.setattr(sync_secrets, "resolve_secret_value", lambda key, *a, **k: ("VALUE-NEVER-PRINTED", "proton"))
    with pytest.raises(SystemExit) as exc:
        sync_secrets.main(["--staging"])
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "FAILED (task: Write each declared credential file)" in output
    assert "VALUE-NEVER-PRINTED" not in output
    assert pushes == ["CI_ONLY", "REPO_WIDE"]


def test_the_cli_gets_the_app_parent_as_workspace_root_unless_webapps_root_is_already_set(tmp_path, monkeypatch):
    app_dir, cli = cli_stub(tmp_path, monkeypatch)
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.delenv("WEBAPPS_ROOT", raising=False)
    ops_delivery.managed_environments(app_dir, run=run)
    ops_delivery.deliver(["staging"], cwd=app_dir, run=run)
    assert [env["WEBAPPS_ROOT"] for env in seen] == [str(tmp_path.resolve())] * 2
    monkeypatch.setenv("WEBAPPS_ROOT", "/explicit/root")
    ops_delivery.managed_environments(app_dir, run=run)
    assert seen[-1]["WEBAPPS_ROOT"] == "/explicit/root"


def test_the_missing_webapp_management_note_is_printed_and_nothing_else_changes_for_a_legacy_app(app, capsys, monkeypatch):
    monkeypatch.delenv("WEBAPP_MANAGEMENT_DIR", raising=False)
    monkeypatch.setattr(ops_delivery, "managed_environments", lambda *a, **k: {})
    sync_secrets.main(["--staging"])
    assert "No webapp-management checkout beside this app" in capsys.readouterr().out
    assert pushed(app, "staging") == ["CI_ONLY", "RUNTIME_SECRET"]
