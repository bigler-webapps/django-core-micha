"""Environment identity guarantees for generated configuration."""

from unittest.mock import Mock

import pytest
import yaml

from django_core_micha.scripts import generate_env as ge


def _configure_generator(tmp_path, monkeypatch, environments, app_env=None, secret_inputs=None):
    monkeypatch.chdir(tmp_path)
    project = {"project_name": "identity-test", "environments": environments}
    if app_env is not None:
        project["app_env"] = app_env

    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    monkeypatch.setattr(ge, "load_secrets_metadata", lambda path="secrets.yaml": ({}, {}))
    loader = Mock(return_value=secret_inputs or {})
    monkeypatch.setattr(ge, "load_inputs_from_secrets_yaml", loader)
    monkeypatch.delenv("SECRETS_CONTEXT", raising=False)
    return config_path


def _generate(tmp_path, env_name, config_path):
    output_path = tmp_path / "generated-output.txt"
    ge.generate_env(
        env_name,
        config_path=str(config_path),
        output_path=str(output_path),
    )
    return output_path


def test_local_identity_is_emitted_once_and_beats_all_inputs(tmp_path, monkeypatch):
    config_path = _configure_generator(
        tmp_path,
        monkeypatch,
        environments={"local": {"env_overrides": {"ENV_TYPE": "from-override", "IDENTITY_TEST_OVERRIDE": "override-marker"}}},
        app_env={"ENV_TYPE": "from-app-env", "IDENTITY_TEST_APP": "app-marker"},
        secret_inputs={"ENV_TYPE": "from-secret", "IDENTITY_TEST_SECRET": "secret-marker"},
    )

    output_path = _generate(tmp_path, "local", config_path)
    lines = output_path.read_text(encoding="utf-8").splitlines()

    assert lines.count("ENV_TYPE=local") == 1
    assert not any(line.startswith("ENV_TYPE=") and line != "ENV_TYPE=local" for line in lines)
    assert "IDENTITY_TEST_OVERRIDE=override-marker" in lines
    assert "IDENTITY_TEST_APP=app-marker" in lines
    assert "IDENTITY_TEST_SECRET=secret-marker" in lines
    ge.load_inputs_from_secrets_yaml.assert_called_once()
    assert ge.load_inputs_from_secrets_yaml.call_args.kwargs["project_config"]["project_name"] == "identity-test"


@pytest.mark.parametrize("env_name", ["staging", "production", "edge"])
def test_non_local_identity_matches_selected_environment(tmp_path, monkeypatch, env_name):
    config_path = _configure_generator(
        tmp_path,
        monkeypatch,
        environments={env_name: {"env_overrides": {"ENV_TYPE": "from-override"}}},
        app_env={"ENV_TYPE": "from-app-env"},
        secret_inputs={"ENV_TYPE": "from-secret"},
    )

    output_path = _generate(tmp_path, env_name, config_path)

    assert [line for line in output_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("ENV_TYPE=")] == [f"ENV_TYPE={env_name}"]


@pytest.mark.parametrize("environments", [{"local": {}}, {}])
def test_undeclared_environment_refuses_generation(tmp_path, monkeypatch, capsys, environments):
    config_path = _configure_generator(
        tmp_path,
        monkeypatch,
        environments=environments,
    )
    output_path = tmp_path / "generated-output.txt"

    with pytest.raises(SystemExit) as exc_info:
        ge.generate_env(
            "preview",
            config_path=str(config_path),
            output_path=str(output_path),
        )

    assert exc_info.value.code == 1
    assert "Environment 'preview' not defined" in capsys.readouterr().out
    assert not output_path.exists()
