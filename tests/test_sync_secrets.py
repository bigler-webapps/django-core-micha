"""Tests for sync_secrets.py — focused on project.yaml-based env/server resolution."""

import pytest

from django_core_micha.scripts.sync_secrets import (
    get_target_scope,
    resolve_github_environment,
    resolve_server_from_project,
    resolve_source,
)


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
