import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings
from django.http import HttpResponse

from django_core_micha.settings._static_files import immutable_file_test


@pytest.mark.parametrize(
    "name",
    [
        "index-jQGc1USQ.js",
        "apiClient-CJL0hVEu.css",
        "dm-sans-latin-400-normal-CW0RaeGs.woff2",
        "apiClient-BFiUcIo-.js",
    ],
)
def test_vite_hashed_top_level_assets_are_immutable(name):
    with override_settings(STATIC_URL="/static/", WHITENOISE_STATIC_PREFIX="/static/"):
        assert immutable_file_test(f"/static/{name}", f"/static/{name}")


@pytest.mark.parametrize(
    "name",
    [
        "index.html",
        "favicon.svg",
        "robots.txt",
        "select2-dropdown.css",
        "OpenSans-SemiBold.ttf",
        "pdf-template-boho-abstract.png",
        "webfont-20261021.woff2",
    ],
)
def test_unhashed_top_level_assets_are_not_immutable(name):
    with override_settings(STATIC_URL="/static/", WHITENOISE_STATIC_PREFIX="/static/"):
        assert not immutable_file_test(f"/static/{name}", f"/static/{name}")


def test_vite_hash_rule_only_applies_directly_below_static_prefix():
    with override_settings(STATIC_URL="/static/", WHITENOISE_STATIC_PREFIX="/static/"):
        assert not immutable_file_test(
            "/static/vendor/index-jQGc1USQ.js",
            "/static/vendor/index-jQGc1USQ.js",
        )
        assert not immutable_file_test(
            "/assets/index-jQGc1USQ.js",
            "/assets/index-jQGc1USQ.js",
        )


def test_static_prefix_is_derived_like_whitenoise_without_an_explicit_prefix():
    from django.conf import settings

    from django_core_micha.settings._static_files import _static_prefix

    assert not hasattr(settings, "WHITENOISE_STATIC_PREFIX")  # tests/settings.py sets none
    with override_settings(STATIC_URL="https://cdn.example.test/app/static/", FORCE_SCRIPT_NAME="/app/"):
        assert _static_prefix(settings) == "/static/"
        assert immutable_file_test("/static/index-jQGc1USQ.js", "/static/index-jQGc1USQ.js")
        assert not immutable_file_test("/app/static/index-jQGc1USQ.js", "/app/static/index-jQGc1USQ.js")
    with override_settings(STATIC_URL=None):
        assert _static_prefix(settings) == "/"  # no crash at WhiteNoise start-up
    with override_settings(STATIC_URL="/static/", WHITENOISE_STATIC_PREFIX=""):
        assert _static_prefix(settings) == "/"  # an explicit prefix wins, as in WhiteNoise


def test_django_manifest_hashed_assets_remain_immutable(tmp_path):
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "app.0123456789ab.css").write_text("body {}", encoding="utf-8")
    (tmp_path / "staticfiles.json").write_text(
        '{"version": "1.1", "paths": {"css/app.css": "css/app.0123456789ab.css"}}',
        encoding="utf-8",
    )
    storages = {
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }

    with override_settings(
        STATIC_ROOT=tmp_path,
        STATIC_URL="/static/",
        WHITENOISE_STATIC_PREFIX="/static/",
        STORAGES=storages,
    ):
        assert immutable_file_test(
            str(tmp_path / "css" / "app.0123456789ab.css"),
            "/static/css/app.0123456789ab.css",
        )
        assert not immutable_file_test(
            str(tmp_path / "css" / "app.css"),
            "/static/css/app.css",
        )


def test_whitenoise_uses_immutable_and_short_cache_headers(tmp_path):
    from whitenoise.middleware import WhiteNoiseMiddleware

    hashed_name = "index-jQGc1USQ.js"
    plain_name = "index.html"
    (tmp_path / hashed_name).write_text("console.log('hashed')", encoding="utf-8")
    (tmp_path / plain_name).write_text("<html></html>", encoding="utf-8")

    with override_settings(
        STATIC_ROOT=tmp_path,
        STATIC_URL="/static/",
        WHITENOISE_STATIC_PREFIX="/static/",
        WHITENOISE_IMMUTABLE_FILE_TEST=immutable_file_test,
    ):
        middleware = WhiteNoiseMiddleware(lambda request: HttpResponse("fallback"))
        factory = RequestFactory()

        hashed_response = middleware(factory.get(f"/static/{hashed_name}"))
        plain_response = middleware(factory.get(f"/static/{plain_name}"))

    assert hashed_response["Cache-Control"] == "max-age=315360000, public, immutable"
    assert plain_response["Cache-Control"] == "max-age=60, public"


@pytest.mark.parametrize("env_type", ["local", "staging"])
def test_settings_base_static_cache_setting_is_non_local_only(tmp_path, env_type):
    settings_module = tmp_path / "consumer_settings.py"
    settings_module.write_text(
        "from django_core_micha.settings.settings_base import *  # noqa: F401,F403\n",
        encoding="utf-8",
    )
    check_script = textwrap.dedent(
        """
        from django.conf import settings

        if settings.ENV_TYPE == "local":
            assert not hasattr(settings, "WHITENOISE_IMMUTABLE_FILE_TEST")
            assert settings.STORAGES["staticfiles"]["BACKEND"] == (
                "whitenoise.storage.CompressedStaticFilesStorage"
            )
            assert settings.WHITENOISE_AUTOREFRESH is True
            assert settings.WHITENOISE_USE_FINDERS is True
        else:
            from django_core_micha.settings._static_files import immutable_file_test
            assert settings.WHITENOISE_IMMUTABLE_FILE_TEST is immutable_file_test
        print("OK")
        """
    )
    src_path = Path(__file__).resolve().parents[1] / "src"
    env = {
        **os.environ,
        "ENV_TYPE": env_type,
        "DJANGO_SETTINGS_MODULE": "consumer_settings",
        "DJANGO_SECRET_KEY": "static-cache-test-secret",
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(src_path))),
    }
    if env_type == "local":
        env.pop("DJANGO_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-c", check_script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"{env_type} settings branch failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
