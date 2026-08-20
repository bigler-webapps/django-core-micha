"""DX-4: run-dev's .env/project.yaml and venv/requirements.txt drift warnings."""
import yaml

from django_core_micha.scripts import generate_env
from django_core_micha.scripts.drift_check import (
    check_env_drift,
    check_venv_drift,
    collect_drift_warnings,
)


def _write_project_yaml(base_dir, app_env=None, env_overrides=None):
    config = {
        "project_name": "testproj",
        "app_env": app_env or {},
        "environments": {
            "local": {
                "domains": [],
                "use_traefik": False,
                "env_overrides": env_overrides or {},
            }
        },
    }
    (base_dir / "project.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def _write_env_file(base_dir, pairs):
    lines = [f"{key}={value}" for key, value in pairs.items()]
    (base_dir / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_dist_info(site_packages, name, version):
    dist_info = site_packages / f"{name}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")


def test_drifted_env_reports_keys_never_values(tmp_path):
    _write_project_yaml(
        tmp_path,
        app_env={"CUSTOM_FLAG": "correct-value"},
        env_overrides={"ANOTHER_FLAG": "override-value"},
    )
    # ANOTHER_FLAG missing entirely; CUSTOM_FLAG present but stale.
    _write_env_file(tmp_path, {"CUSTOM_FLAG": "stale-value"})

    warnings = check_env_drift(tmp_path, "local")

    joined = " ".join(warnings)
    assert "CUSTOM_FLAG" in joined
    assert "ANOTHER_FLAG" in joined
    # The whole point of the check: never leak the actual values into output.
    assert "correct-value" not in joined
    assert "override-value" not in joined
    assert "stale-value" not in joined


def test_drifted_venv_detects_missing_and_below_pin(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "requirements.txt").write_text(
        "belowpin==2.0.0\nmissingpkg>=1.0.0\n", encoding="utf-8"
    )
    site_packages = backend / ".venv" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    _write_dist_info(site_packages, "belowpin", "1.0.0")

    warnings = check_venv_drift(tmp_path)

    joined = " ".join(warnings)
    assert "missingpkg" in joined
    assert "belowpin" in joined
    assert "1.0.0" in joined  # package versions are not secrets -- fine to show


def test_clean_setup_produces_no_output(tmp_path):
    assert check_env_drift(tmp_path, "local") == []
    assert check_venv_drift(tmp_path) == []
    assert collect_drift_warnings(tmp_path, "local") == []


def test_env_generated_by_generate_env_itself_reports_clean(tmp_path):
    """Platform-computed keys (DJANGO_ALLOWED_HOSTS, CSRF_TRUSTED_URLS, ...) must
    not produce a false positive just because they're derived, not copied."""
    _write_project_yaml(
        tmp_path,
        app_env={"CUSTOM_FLAG": "value-1"},
        env_overrides={"ANOTHER_FLAG": "value-2"},
    )
    generate_env.generate_env(
        "local",
        config_path=str(tmp_path / "project.yaml"),
        output_path=str(tmp_path / ".env"),
    )

    assert check_env_drift(tmp_path, "local") == []


def test_malformed_project_yaml_degrades_to_no_warnings(tmp_path):
    (tmp_path / "project.yaml").write_text("not: [valid: yaml: at: all", encoding="utf-8")
    (tmp_path / ".env").write_text("SOMETHING=1\n", encoding="utf-8")

    assert check_env_drift(tmp_path, "local") == []


def test_missing_or_unreadable_venv_degrades_to_no_warnings(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "requirements.txt").write_text("somepkg==1.0.0\n", encoding="utf-8")
    # No .venv directory at all -- the common case for most apps on this estate.
    assert check_venv_drift(tmp_path) == []

    # .venv exists but has no site-packages directory in either layout.
    (backend / ".venv").mkdir()
    assert check_venv_drift(tmp_path) == []
