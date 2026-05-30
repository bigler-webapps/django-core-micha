"""Tests for generate_env app_env support (project.yaml global env block)."""
import yaml

from django_core_micha.scripts import generate_env as ge


def _parse_env(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def _write_project(tmp_path, project):
    (tmp_path / "secrets.yaml").write_text("config: {}\nsecrets: {}\n", encoding="utf-8")
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(project), encoding="utf-8")


def test_app_env_propagates_to_non_local_and_env_overrides_win(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(
        tmp_path,
        {
            "project_name": "t",
            "app_env": {"DB_NAME": "shared_db", "EMAIL_PORT": "587"},
            "environments": {
                "production": {
                    "use_traefik": True,
                    "domains": ["example.com"],
                    "env_overrides": {"DB_NAME": "prod_db"},
                }
            },
        },
    )

    ge.generate_env("production", config_path=str(tmp_path / "project.yaml"),
                    output_path=str(tmp_path / ".env"))

    env = _parse_env(tmp_path / ".env")
    assert env["EMAIL_PORT"] == "587"   # app_env reaches prod .env
    assert env["DB_NAME"] == "prod_db"  # env_overrides beats app_env


def test_platform_computed_keys_are_not_overridable_by_app_env(tmp_path, monkeypatch):
    # Platform-computed keys (emitted via add()) are authoritative; an app_env
    # entry for such a key must NOT win. Documents the real precedence contract.
    monkeypatch.chdir(tmp_path)
    _write_project(
        tmp_path,
        {
            "project_name": "t",
            "app_env": {"DJANGO_ALLOWED_HOSTS": "evil.example", "BACKUP_ENABLE": "false"},
            "environments": {
                "production": {"use_traefik": True, "domains": ["example.com"]}
            },
        },
    )

    ge.generate_env("production", config_path=str(tmp_path / "project.yaml"),
                    output_path=str(tmp_path / ".env"))

    env = _parse_env(tmp_path / ".env")
    assert env["DJANGO_ALLOWED_HOSTS"] != "evil.example"  # computed value wins
    assert "example.com" in env["DJANGO_ALLOWED_HOSTS"]
    assert env["BACKUP_ENABLE"] == "true"                 # computed (production) wins


def test_missing_app_env_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(
        tmp_path,
        {
            "project_name": "t",
            "environments": {"production": {"use_traefik": True, "domains": ["example.com"]}},
        },
    )

    ge.generate_env("production", config_path=str(tmp_path / "project.yaml"),
                    output_path=str(tmp_path / ".env"))

    env = _parse_env(tmp_path / ".env")
    assert env["PROJECT_NAME"] == "t"   # runs cleanly, v1 behaviour intact
    assert "DB_NAME" not in env          # nothing injected without app_env
