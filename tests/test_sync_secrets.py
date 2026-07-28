"""Tests for sync_secrets.py — focused on project.yaml-based env/server resolution."""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from django_core_micha.scripts.sync_secrets import (
    get_proton_secret,
    get_target_scope,
    main,
    resolve_github_environment,
    resolve_server_from_project,
    resolve_source,
)

_MINIMAL_SECRETS_YAML = (
    "config:\n"
    "  target_repo: org/repo\n"
    "secrets:\n"
    "  MY_SECRET:\n"
    "    source: proton://Vault/Item/field\n"
)


@pytest.fixture()
def secrets_dir(tmp_path, monkeypatch):
    (tmp_path / "secrets.yaml").write_text(_MINIMAL_SECRETS_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# resolve_server_from_project
# ---------------------------------------------------------------------------


def test_resolve_server_from_project_returns_server_value():
    project_config = {
        "environments": {
            "production": {"server": "main-prod"},
            "staging": {"server": "staging"},
        }
    }
    assert resolve_server_from_project(project_config, "production") == "main-prod"
    assert resolve_server_from_project(project_config, "staging") == "staging"


def test_resolve_server_from_project_returns_none_for_missing_env():
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    assert resolve_server_from_project(project_config, "ghost") is None


def test_resolve_server_from_project_returns_none_for_missing_server_field():
    project_config = {"environments": {"production": {"domains": ["x.example"]}}}
    assert resolve_server_from_project(project_config, "production") is None


def test_resolve_server_from_project_handles_empty_inputs():
    assert resolve_server_from_project(None, "production") is None
    assert resolve_server_from_project({}, "production") is None
    assert resolve_server_from_project({"environments": {}}, "production") is None
    assert resolve_server_from_project({"environments": {"production": {"server": "main-prod"}}}, None) is None


def test_resolve_server_from_project_strips_whitespace():
    project_config = {"environments": {"production": {"server": "  main-prod  "}}}
    assert resolve_server_from_project(project_config, "production") == "main-prod"


def test_resolve_server_from_project_rejects_non_mapping_env():
    project_config = {"environments": {"production": "not-a-dict"}}
    assert resolve_server_from_project(project_config, "production") is None


# ---------------------------------------------------------------------------
# resolve_source — {server} placeholder
# ---------------------------------------------------------------------------


def test_resolve_source_static_source_ignores_project_config():
    definition = {"source": "proton://Vault/Item/field"}
    assert resolve_source(definition, {}, "production", project_config={"environments": {}}) == \
        "proton://Vault/Item/field"


def test_resolve_source_target_placeholder_only():
    definition = {"source_template": "proton://Vault/Item-{target}/field"}
    assert resolve_source(definition, {}, "production") == "proton://Vault/Item-production/field"


def test_resolve_source_server_placeholder_resolves_via_project_config():
    definition = {"source_template": "proton://Vault/Item-{server}/field"}
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    assert resolve_source(definition, {}, "production", project_config=project_config) == \
        "proton://Vault/Item-main-prod/field"


def test_resolve_source_server_placeholder_without_project_config_fails(capsys):
    definition = {"source_template": "proton://Vault/Item-{server}/field"}
    result = resolve_source(definition, {}, "production", project_config=None)
    assert result is None
    captured = capsys.readouterr()
    assert "{server}" in captured.out


def test_resolve_source_server_placeholder_with_missing_env_fails(capsys):
    definition = {"source_template": "proton://Vault/Item-{server}/field"}
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    result = resolve_source(definition, {}, "ghost", project_config=project_config)
    assert result is None
    captured = capsys.readouterr()
    assert "{server}" in captured.out


def test_resolve_source_both_placeholders_combined():
    definition = {"source_template": "proton://Vault-{target}/Item-{server}/field"}
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    assert resolve_source(definition, {}, "production", project_config=project_config) == \
        "proton://Vault-production/Item-main-prod/field"


def test_resolve_source_missing_target_returns_none(capsys):
    definition = {"source_template": "proton://Vault/Item-{target}/field"}
    result = resolve_source(definition, {}, None)
    assert result is None
    captured = capsys.readouterr()
    assert "without a secret target" in captured.out


def test_resolve_source_unknown_placeholder_returns_none(capsys):
    definition = {"source_template": "proton://Vault/Item-{unknown}/field"}
    result = resolve_source(definition, {}, "production")
    assert result is None
    captured = capsys.readouterr()
    assert "Invalid source_template placeholder" in captured.out


# ---------------------------------------------------------------------------
# resolve_github_environment — project.yaml opt-in
# ---------------------------------------------------------------------------


def test_resolve_github_environment_explicit_override_wins():
    result = resolve_github_environment(
        {"use_project_yaml": True},
        secret_target="production",
        github_environment="override-env",
        project_config={"environments": {"production": {}}},
    )
    assert result == "override-env"


def test_resolve_github_environment_via_project_yaml_when_opted_in():
    config = {"use_project_yaml": True}
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    assert resolve_github_environment(config, secret_target="production", project_config=project_config) == "production"


def test_resolve_github_environment_project_yaml_without_opt_in_skipped():
    """Without use_project_yaml=true the project-config branch must NOT trigger."""
    config = {}  # no use_project_yaml
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    assert resolve_github_environment(config, secret_target="production", project_config=project_config) is None


def test_resolve_github_environment_project_yaml_unmatched_env_falls_through():
    """Opt-in but target not in project.yaml envs → falls back to other resolvers."""
    config = {"use_project_yaml": True, "github_environment_template": "{target}-fallback"}
    project_config = {"environments": {"production": {"server": "main-prod"}}}
    # ghost is not in project envs, so we fall through to the template
    assert resolve_github_environment(config, secret_target="ghost", project_config=project_config) == "ghost-fallback"


def test_resolve_github_environment_static_fallback_when_nothing_resolves():
    config = {"github_environment": "static-env"}
    assert resolve_github_environment(config, secret_target=None) == "static-env"


def test_resolve_github_environment_returns_none_for_repo_level():
    """No inventory, no project-yaml opt-in, no template, no static → repo-level."""
    assert resolve_github_environment({}, secret_target="production") is None


# ---------------------------------------------------------------------------
# Backward compat: inventory_path / github_environment_template flows
# ---------------------------------------------------------------------------


def test_resolve_github_environment_template_target_substitution():
    config = {"github_environment_template": "deploy-{target}"}
    assert resolve_github_environment(config, secret_target="main-prod") == "deploy-main-prod"


def test_resolve_github_environment_template_missing_target_returns_none(capsys):
    config = {"github_environment_template": "deploy-{target}"}
    assert resolve_github_environment(config, secret_target=None) is None
    captured = capsys.readouterr()
    assert "without a secret target" in captured.out


# ---------------------------------------------------------------------------
# get_target_scope — per-secret push scope override
# ---------------------------------------------------------------------------


def test_get_target_scope_defaults_to_env():
    assert get_target_scope({}) == "env"


def test_get_target_scope_honors_explicit_env():
    assert get_target_scope({"target_scope": "env"}) == "env"


def test_get_target_scope_honors_repo():
    assert get_target_scope({"target_scope": "repo"}) == "repo"


def test_get_target_scope_rejects_invalid_value(capsys):
    with pytest.raises(SystemExit):
        get_target_scope({"target_scope": "global"}, key="MY_SECRET")
    captured = capsys.readouterr()
    assert "invalid target_scope" in captured.out
    assert "MY_SECRET" in captured.out
    assert "allowed: env, repo" in captured.out


# ---------------------------------------------------------------------------
# main() — bare invocation and shorthands
# ---------------------------------------------------------------------------


def test_bare_invocation_syncs_staging_then_production(secrets_dir):
    """No arguments → sync_github called twice: staging first, production second."""
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        main([])

    assert mock_sync.call_count == 2
    targets = [c.kwargs["secret_target"] for c in mock_sync.call_args_list]
    assert targets == ["staging", "production"]


def _write_local_project(tmp_path):
    (tmp_path / "project.yaml").write_text(
        "project_name: matrix-app\n"
        "app_env:\n"
        "  APP_SETTING: from-project\n"
        "environments:\n"
        "  local:\n"
        "    use_traefik: true\n"
        "    domains: [local.example.test]\n"
        "    server: local-dev\n"
        "    web_port: 8123\n"
        "  staging: {}\n"
        "  production: {}\n",
        encoding="utf-8",
    )


def test_bare_invocation_writes_composed_local_env_then_syncs_all_github_targets(secrets_dir):
    """No flags writes full local composition before the all-target GitHub loop."""
    _write_local_project(secrets_dir)
    with patch("django_core_micha.scripts.sync_secrets._do_server_sync") as mock_server_sync:
        main([])

    local_env = (secrets_dir / ".env").read_text(encoding="utf-8")
    assert "PROJECT_NAME=matrix-app" in local_env
    assert "TRAEFIK_ROUTER_RULE=Host(`local.example.test`)" in local_env
    assert "APP_SETTING=from-project" in local_env
    assert not (secrets_dir / ".env.local").exists()
    assert [call.args[0] for call in mock_server_sync.call_args_list] == ["staging", "production"]


def test_local_writes_only_full_composed_env_and_never_env_local(secrets_dir):
    """--local delegates to generate-env and makes no GitHub call."""
    _write_local_project(secrets_dir)
    with patch("django_core_micha.scripts.sync_secrets._do_server_sync") as mock_server_sync:
        main(["--local"])

    local_env = (secrets_dir / ".env").read_text(encoding="utf-8")
    assert "PROJECT_NAME=matrix-app" in local_env
    assert "WEB_PORT=8123" in local_env
    assert "TRAEFIK_ROUTER_RULE=Host(`local.example.test`)" in local_env
    assert not (secrets_dir / ".env.local").exists()
    mock_server_sync.assert_not_called()


@pytest.mark.parametrize("flag", ["--github", "--remote"])
def test_github_all_spellings_sync_all_targets_without_touching_local_env(secrets_dir, flag):
    """--github and --remote preserve a pre-existing local .env untouched."""
    _write_local_project(secrets_dir)
    (secrets_dir / ".env").write_text("SENTINEL=unchanged\n", encoding="utf-8")
    with patch("django_core_micha.scripts.sync_secrets._do_server_sync") as mock_server_sync:
        main([flag])

    assert (secrets_dir / ".env").read_text(encoding="utf-8") == "SENTINEL=unchanged\n"
    assert not (secrets_dir / ".env.local").exists()
    assert [call.args[0] for call in mock_server_sync.call_args_list] == ["staging", "production"]


def test_bare_mode_without_local_environment_skips_local_and_still_syncs(secrets_dir, capsys):
    """No flags remains non-fatal for GitHub-only projects without local config."""
    (secrets_dir / "project.yaml").write_text("environments:\n  staging: {}\n", encoding="utf-8")
    with patch("django_core_micha.scripts.sync_secrets._do_server_sync") as mock_server_sync:
        main([])

    assert "skipping local .env write" in capsys.readouterr().out
    assert [call.args[0] for call in mock_server_sync.call_args_list] == ["staging", "production"]


def test_local_without_local_environment_is_fatal(secrets_dir, capsys):
    """Explicit --local reports a clear error when project.yaml lacks local."""
    (secrets_dir / "project.yaml").write_text("environments:\n  staging: {}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["--local"])

    assert exc_info.value.code == 1
    assert "no 'local' environment" in capsys.readouterr().out


def test_local_without_proton_uses_dev_default(secrets_dir, monkeypatch):
    """The generate-env local path degrades cleanly when Proton is unavailable."""
    _write_local_project(secrets_dir)
    (secrets_dir / "secrets.yaml").write_text(
        "config:\n"
        "  target_repo: org/repo\n"
        "secrets:\n"
        "  MY_SECRET:\n"
        "    source: proton://Vault/Item/field\n"
        "    dev_default: local-default\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("django_core_micha.scripts.sync_secrets.shutil.which", lambda _cmd: None)

    main(["--local"])

    assert "MY_SECRET=local-default" in (secrets_dir / ".env").read_text(encoding="utf-8")
    assert not (secrets_dir / ".env.local").exists()


def test_local_without_proton_and_without_default_degrades_to_empty_value(secrets_dir, monkeypatch):
    """A missing local Proton value warns and writes a regenerable empty value."""
    _write_local_project(secrets_dir)
    monkeypatch.setattr("django_core_micha.scripts.sync_secrets.shutil.which", lambda _cmd: None)

    main(["--local"])

    assert "MY_SECRET=" in (secrets_dir / ".env").read_text(encoding="utf-8")
    assert not (secrets_dir / ".env.local").exists()


def test_local_resolves_server_template_using_project_config(secrets_dir):
    """The delegated local generator retains {server} source-template resolution."""
    _write_local_project(secrets_dir)
    (secrets_dir / "secrets.yaml").write_text(
        "config:\n"
        "  target_repo: org/repo\n"
        "secrets:\n"
        "  MY_SECRET:\n"
        "    source_template: proton://Vault/Item-{server}/field\n",
        encoding="utf-8",
    )
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.get_proton_secret", return_value="from-proton") as mock_get,
    ):
        # {server} resolves via environments[<secret_target>].server — secret_target
        # is orthogonal to env_name="local" and must be requested explicitly, exactly
        # as it always did for the pre-existing GitHub/local secret paths.
        main(["--local", "--secret-target", "local"])

    mock_get.assert_called_once_with("proton://Vault/Item-local-dev/field")
    assert "MY_SECRET=from-proton" in (secrets_dir / ".env").read_text(encoding="utf-8")


def test_bare_staging_failure_aborts_production(secrets_dir):
    """Staging failure → production never runs, exit code non-zero."""
    call_count = 0

    def _fail_on_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            sys.exit(1)

    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github", side_effect=_fail_on_first),
    ):
        with pytest.raises(SystemExit) as exc_info:
            main([])

    assert exc_info.value.code != 0
    assert call_count == 1


def test_bare_invocation_honours_config_bare_server_targets(secrets_dir):
    """config.bare_server_targets overrides the default staging/production pair."""
    (secrets_dir / "secrets.yaml").write_text(
        "config:\n"
        "  target_repo: org/repo\n"
        "  bare_server_targets: [staging, main-prod, contact-prod, innoservice-prod]\n"
        "secrets:\n"
        "  MY_SECRET:\n"
        "    source: proton://Vault/Item/field\n",
        encoding="utf-8",
    )
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        main([])

    targets = [c.kwargs["secret_target"] for c in mock_sync.call_args_list]
    assert targets == ["staging", "main-prod", "contact-prod", "innoservice-prod"]


@pytest.mark.parametrize(
    "value",
    [
        "  bare_server_targets: []\n",  # empty list
        "  bare_server_targets: staging\n",  # scalar, not a list
        "  bare_server_targets: ['', main-prod]\n",  # blank entry
        "  bare_server_targets: [staging, 3]\n",  # non-string entry
    ],
)
def test_bare_invocation_rejects_invalid_bare_server_targets(secrets_dir, value):
    """Malformed config.bare_server_targets aborts before any sync."""
    (secrets_dir / "secrets.yaml").write_text(
        "config:\n"
        "  target_repo: org/repo\n"
        f"{value}"
        "secrets:\n"
        "  MY_SECRET:\n"
        "    source: proton://Vault/Item/field\n",
        encoding="utf-8",
    )
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        with pytest.raises(SystemExit) as exc_info:
            main([])

    assert exc_info.value.code != 0
    mock_sync.assert_not_called()


def test_explicit_server_secret_target_staging_unchanged(secrets_dir):
    """--server --secret-target staging: single call, target=staging (regression)."""
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        main(["--server", "--secret-target", "staging"])

    mock_sync.assert_called_once()
    assert mock_sync.call_args.kwargs["secret_target"] == "staging"


def test_explicit_server_secret_target_production_unchanged(secrets_dir):
    """--server --secret-target production: single call, target=production (regression)."""
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        main(["--server", "--secret-target", "production"])

    mock_sync.assert_called_once()
    assert mock_sync.call_args.kwargs["secret_target"] == "production"


def test_staging_shorthand(secrets_dir):
    """--staging: single call equivalent to --server --secret-target staging."""
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        main(["--staging"])

    mock_sync.assert_called_once()
    assert mock_sync.call_args.kwargs["secret_target"] == "staging"


def test_production_shorthand(secrets_dir):
    """--production: single call equivalent to --server --secret-target production."""
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=True),
        patch("django_core_micha.scripts.sync_secrets.sync_github") as mock_sync,
    ):
        main(["--production"])

    mock_sync.assert_called_once()
    assert mock_sync.call_args.kwargs["secret_target"] == "production"


def test_bare_with_secret_target_errors(secrets_dir, capsys):
    """--secret-target without a destination flag must error, not silently drop the override."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--secret-target", "custom"])
    assert exc_info.value.code != 0


def test_staging_shorthand_combined_with_secret_target_errors(secrets_dir):
    """--staging --secret-target X must error to prevent silent discard of --secret-target."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--staging", "--secret-target", "production"])
    assert exc_info.value.code != 0


def test_production_shorthand_combined_with_secret_target_errors(secrets_dir):
    """--production --secret-target X must error to prevent silent discard of --secret-target."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--production", "--secret-target", "staging"])
    assert exc_info.value.code != 0


@pytest.mark.parametrize("flag", ["--github", "--remote"])
def test_github_all_combined_with_secret_target_errors(secrets_dir, flag):
    """--github/--remote sync every bare target; combining with --secret-target is ambiguous."""
    with pytest.raises(SystemExit) as exc_info:
        main([flag, "--secret-target", "staging"])
    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# get_proton_secret — retry-with-backoff
# ---------------------------------------------------------------------------

_PROTON_PASS_JSON = '{"item": {"content": {"password": "secret-value"}}}'
_OK = SimpleNamespace(returncode=0, stdout=_PROTON_PASS_JSON, stderr="")
_FAIL = SimpleNamespace(returncode=1, stdout="", stderr="error")


def test_get_proton_secret_immediate_success():
    """Single successful fetch → value returned, subprocess called once."""
    with (
        patch("django_core_micha.scripts.sync_secrets.subprocess.run", return_value=_OK) as mock_run,
        patch("django_core_micha.scripts.sync_secrets.time.sleep"),
    ):
        result = get_proton_secret("proton://Vault/Item/password")
    assert result == "secret-value"
    assert mock_run.call_count == 1


def test_get_proton_secret_retries_then_succeeds(capsys):
    """Fail twice, succeed on third attempt → value returned, no [CLI ERROR]."""
    with (
        patch(
            "django_core_micha.scripts.sync_secrets.subprocess.run",
            side_effect=[_FAIL, _FAIL, _OK],
        ) as mock_run,
        patch("django_core_micha.scripts.sync_secrets.time.sleep") as mock_sleep,
    ):
        result = get_proton_secret("proton://Vault/Item/password")
    assert result == "secret-value"
    assert mock_run.call_count == 3
    assert mock_sleep.call_count == 2  # sleep between each failed attempt, not after success
    captured = capsys.readouterr()
    assert "[CLI ERROR]" not in captured.out
    assert "[retry 1/3]" in captured.out
    assert "[retry 2/3]" in captured.out
    assert "[OK]" in captured.out


def test_get_proton_secret_all_retries_fail_returns_none(capsys):
    """All 3 attempts fail → None returned, [CLI ERROR] printed, skip semantics preserved."""
    with (
        patch(
            "django_core_micha.scripts.sync_secrets.subprocess.run",
            side_effect=[_FAIL, _FAIL, _FAIL],
        ) as mock_run,
        patch("django_core_micha.scripts.sync_secrets.time.sleep"),
    ):
        result = get_proton_secret("proton://Vault/Item/password")
    assert result is None  # clobber guard: never an empty string
    assert mock_run.call_count == 3
    captured = capsys.readouterr()
    assert "[CLI ERROR]" in captured.out


def test_get_proton_secret_no_empty_push_on_failure():
    """Clobber guard: CLI error always returns None, not '' or any falsy non-None."""
    with (
        patch("django_core_micha.scripts.sync_secrets.subprocess.run", return_value=_FAIL),
        patch("django_core_micha.scripts.sync_secrets.time.sleep"),
    ):
        result = get_proton_secret("proton://Vault/Item/password")
    assert result is None


def test_sync_github_push_retries_then_succeeds(capsys):
    """A transient GitHub CLI failure is retried before marking the push OK."""
    config = {"target_repo": "org/repo"}
    secrets_def = {"MY_SECRET": {}}
    with (
        patch(
            "django_core_micha.scripts.sync_secrets.subprocess.run",
            side_effect=[_FAIL, _FAIL, _OK],
        ) as mock_run,
        patch("django_core_micha.scripts.sync_secrets.time.sleep"),
    ):
        from django_core_micha.scripts.sync_secrets import sync_github

        sync_github(
            config,
            secrets_def,
            has_proton=False,
            secret_target="staging",
            secret_source="yaml",
            values_data={"targets": {"staging": {"MY_SECRET": "secret-value"}}},
        )

    assert mock_run.call_count == 3
    assert "[OK via yaml]" in capsys.readouterr().out


def test_sync_github_push_persistent_failure_exits_without_secret_value(capsys):
    """Persistent push failures expose key names but never secret values."""
    config = {"target_repo": "org/repo"}
    secrets_def = {"MY_SECRET": {}}
    secret_value = "secret-value-must-not-appear"
    with (
        patch("django_core_micha.scripts.sync_secrets.subprocess.run", return_value=_FAIL),
        patch("django_core_micha.scripts.sync_secrets.time.sleep"),
        pytest.raises(SystemExit) as exc_info,
    ):
        from django_core_micha.scripts.sync_secrets import sync_github

        sync_github(
            config,
            secrets_def,
            has_proton=False,
            secret_target="staging",
            secret_source="yaml",
            values_data={"targets": {"staging": {"MY_SECRET": secret_value}}},
        )

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "MY_SECRET" in captured.out
    assert secret_value not in captured.out


# ---------------------------------------------------------------------------
# Bare-mode separator — Unicode / cp1252 safety
# ---------------------------------------------------------------------------


def test_bare_mode_separator_no_unicode_crash(secrets_dir, capsys):
    """Separator must not contain U+2500 (BOX DRAWINGS LIGHT HORIZONTAL), which crashes cp1252."""
    with (
        patch("django_core_micha.scripts.sync_secrets.check_dependencies", return_value=False),
        patch("django_core_micha.scripts.sync_secrets.sync_github"),
    ):
        main([])
    captured = capsys.readouterr()
    assert "─" not in captured.out  # ─ was the cp1252 crasher
