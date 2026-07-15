"""Regression test for the packaging gap fixed in pyproject.toml `dependencies`.

`tests/settings.py` deliberately does NOT import `settings_base` (it re-declares
a minimal INSTALLED_APPS/MIDDLEWARE by hand — see test_channel_layer.py), so the
main test run never actually exercises `settings_base.py`'s own top-level
imports (`corsheaders`) or its lazily-referenced `MIDDLEWARE`/`STORAGES` backend
strings (`whitenoise`). `django.setup()` alone does not import MIDDLEWARE/STORAGES
entries either — those are only resolved lazily when a handler/storage is
actually built. That double blind spot is exactly why the whitenoise gap (and,
earlier, the allauth/corsheaders gap) went unnoticed until a real consumer
install broke. This test closes it by importing `settings_base` for real and
explicitly resolving every MIDDLEWARE and STORAGES backend string, in an
isolated subprocess (so ENV_TYPE=local can be set without affecting other
tests), exactly as a consuming app's own settings.py does via
`from django_core_micha.settings.settings_base import *`.
"""
import os
import subprocess
import sys
import textwrap


def test_settings_base_is_importable_with_only_declared_dependencies(tmp_path):
    settings_module = tmp_path / "consumer_settings.py"
    settings_module.write_text(
        textwrap.dedent(
            """
            from django_core_micha.settings.settings_base import *  # noqa: F401,F403

            ROOT_URLCONF = "django.urls"
            """
        ),
        encoding="utf-8",
    )

    check_script = textwrap.dedent(
        """
        import django
        django.setup()

        from django.conf import settings
        from django.utils.module_loading import import_string

        # django.setup() only populates INSTALLED_APPS — it does not import
        # MIDDLEWARE or STORAGES backends (those are resolved lazily on first
        # use). Explicitly resolve every one so a missing package (e.g.
        # whitenoise) fails here instead of silently passing.
        for entry in settings.MIDDLEWARE:
            import_string(entry)

        for storage in settings.STORAGES.values():
            import_string(storage["BACKEND"])

        print("OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", check_script],
        cwd=tmp_path,
        env={
            **os.environ,
            "ENV_TYPE": "local",
            "DJANGO_SETTINGS_MODULE": "consumer_settings",
            "PYTHONPATH": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"Importing settings_base and resolving its MIDDLEWARE/STORAGES with "
        f"only pyproject.toml's declared dependencies failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
