"""Boot and feature error checks for optional runtime extras."""

import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_core_apps_boot_when_optional_packages_are_unimportable(tmp_path):
    settings_module = tmp_path / "consumer_settings.py"
    settings_module.write_text(
        textwrap.dedent(
            """
            from django_core_micha.settings.settings_base import *  # noqa: F401,F403

            ROOT_URLCONF = "django.urls"
            DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
            CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
            DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
            """
        ),
        encoding="utf-8",
    )
    check_script = textwrap.dedent(
        """
        import importlib
        import importlib.util
        import sys

        blocked = {"openai", "anthropic", "pypdf", "PIL", "pywebpush", "yaml"}

        class BlockOptionalPackages:
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".", 1)[0] in blocked:
                    raise ModuleNotFoundError(f"blocked optional package: {fullname}")
                return None

        sys.meta_path.insert(0, BlockOptionalPackages())

        import django_core_micha.settings.settings_base as settings_base
        import django
        django.setup()

        from django.apps import apps
        from django.conf import settings
        for app_path in settings_base.CORE_APPS:
            importlib.import_module(app_path)
        for app_config in apps.get_app_configs():
            importlib.import_module(app_config.__class__.__module__)
            models_module = f"{app_config.name}.models"
            if importlib.util.find_spec(models_module) is not None:
                importlib.import_module(models_module)
        importlib.import_module("django_core_micha.api_urls")
        print("OK")
        """
    )
    source_root = PROJECT_ROOT / "src"
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    subprocess_env = {
        **os.environ,
        "ENV_TYPE": "local",
        "DJANGO_SETTINGS_MODULE": "consumer_settings",
        "PYTHONPATH": os.pathsep.join(
            part for part in (str(tmp_path), str(source_root), existing_pythonpath) if part
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", check_script],
        cwd=tmp_path,
        env=subprocess_env,
        capture_output=True,
        text=True,
        timeout=45,
    )

    assert result.returncode == 0, (
        "Core app boot attempted to import an optional package or otherwise failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_ai_feature_without_extra_names_required_extra(monkeypatch):
    from django_core_micha.assistant import turns
    from django_core_micha.assistant.drivers.base import DriverError

    class BlockAI:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".", 1)[0] == "openai":
                raise ModuleNotFoundError(f"blocked optional package: {fullname}")
            return None

    blocker = BlockAI()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    # An earlier test may have imported openai already: a None entry makes the import fail regardless.
    for name in [key for key in sys.modules if key == "openai" or key.startswith("openai.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(DriverError, match=r"django-core-micha\[ai\]") as error:
        turns._make_driver("openai_api", "test-credential")
    assert error.value.code == "not_configured"


def test_extraction_image_without_extra_names_required_extra(monkeypatch):
    from django_core_micha.extraction.errors import DocumentExtractionError
    from django_core_micha.extraction.input_prep import resize_image_bytes

    # A None entry makes the import fail even when PIL was already imported by an earlier test.
    for name in [key for key in sys.modules if key == "PIL" or key.startswith("PIL.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(DocumentExtractionError, match=r"django-core-micha\[extraction\]"):
        resize_image_bytes(b"image-bytes", "image/png")


def test_postgres_backend_uses_psycopg3_and_psycopg2_is_not_declared():
    from django.db.backends.postgresql.base import Database

    assert Database.__name__ == "psycopg"

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]
    declared = list(project["dependencies"])
    for extra_dependencies in project["optional-dependencies"].values():
        declared.extend(extra_dependencies)
    assert all(
        dependency.split("[", 1)[0].split("=", 1)[0].lower() != "psycopg2-binary"
        for dependency in declared
    )


def test_moved_packages_are_extras_only_not_core_dependencies():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]

    def names(rows):
        return {row.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip().lower() for row in rows}

    moved = {"openai", "anthropic", "pypdf", "pillow", "pywebpush", "pyyaml"}
    assert not names(project["dependencies"]) & moved
    extras = project["optional-dependencies"]
    assert names(extras["ai"]) == {"openai", "anthropic"}
    assert names(extras["extraction"]) == {"pypdf", "pillow"}
    assert names(extras["push"]) == {"pywebpush"} and names(extras["scripts"]) == {"pyyaml"}
    assert {"psycopg", "redis"} <= names(project["dependencies"])


def test_gated_features_still_work_when_the_extras_are_installed():
    import io

    from PIL import Image

    from django_core_micha.assistant.turns import _make_driver
    from django_core_micha.extraction.input_prep import resize_image_bytes

    assert _make_driver("openai_api", "sk-test") is not None
    assert _make_driver("anthropic_api", "sk-test") is not None
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    assert resize_image_bytes(buffer.getvalue(), "image/png")
