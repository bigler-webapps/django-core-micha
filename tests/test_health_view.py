"""Tests for the extended /api/healthz endpoint (views.py).

Coverage:
- backward compat: db + cache shape and 200/503 unchanged
- migrations check: pending → ok=False + error; clean → ok=True
- config check: missing keys → ok=False + name-only list; present → ok=True; no value leak
- version field: APP_GIT_SHA set/unset/empty; null in JSON when absent
- db failure: generic error text, no driver-level details in response
"""
from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from django_core_micha.health.views import (
    _check_config,
    _check_db,
    _check_migrations,
    _get_version_info,
    healthz_view,
)


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings_mock(**overrides):
    """Build a MagicMock that mimics the Django settings object."""
    defaults = {
        "EMAIL_PROVIDER": "",
        "AUTH_METHODS": {"social_login": False},
        "ANYMAIL": {},
        "SOCIALACCOUNT_PROVIDERS": {},
        "HEALTHZ_CHECK_TIMEOUT_SECONDS": 3.0,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _patched_view(rf, *, migration_plan=None, **settings_kwargs):
    """Call healthz_view with mocked MigrationExecutor and settings."""
    plan = [] if migration_plan is None else migration_plan
    with (
        patch("django_core_micha.health.views.MigrationExecutor") as MockExec,
        patch("django_core_micha.health.views.settings", _settings_mock(**settings_kwargs)),
    ):
        MockExec.return_value.loader.graph.leaf_nodes.return_value = []
        MockExec.return_value.migration_plan.return_value = plan
        return healthz_view(rf.get("/api/healthz"))


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    @pytest.mark.django_db
    def test_200_all_ok(self, rf):
        response = _patched_view(rf)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "ok"
        assert set(data["checks"]) == {"db", "cache", "migrations", "config"}
        assert all(set(check) == {"ok", "duration_ms"} for check in data["checks"].values())

    @pytest.mark.django_db
    def test_db_shape_unchanged(self, rf):
        response = _patched_view(rf)
        db = json.loads(response.content)["checks"]["db"]
        assert db["ok"] is True
        assert "duration_ms" in db
        assert "error" not in db

    @pytest.mark.django_db
    def test_cache_shape_unchanged(self, rf):
        response = _patched_view(rf)
        cache_check = json.loads(response.content)["checks"]["cache"]
        assert cache_check["ok"] is True
        assert "duration_ms" in cache_check
        assert "error" not in cache_check

    @pytest.mark.django_db
    def test_503_when_any_check_fails(self, rf):
        fake = MagicMock()
        fake.app_label = "testapp"
        fake.name = "0001_initial"
        response = _patched_view(rf, migration_plan=[(fake, False)])
        assert response.status_code == 503
        assert json.loads(response.content)["status"] == "degraded"

    @pytest.mark.django_db
    def test_version_key_always_present(self, rf):
        response = _patched_view(rf)
        data = json.loads(response.content)
        assert "version" in data  # present regardless of value


@pytest.mark.django_db
def test_healthz_cache_check_timeout_returns_fast_degraded_response(rf):
    """A hung cache operation times out without delaying the other checks."""
    class HangingCache:
        def set(self, *args, **kwargs):
            time.sleep(10)

    started = time.monotonic()
    with (
        patch("django_core_micha.health.views.MigrationExecutor") as MockExec,
        patch("django_core_micha.health.views.cache", HangingCache()),
    ):
        MockExec.return_value.loader.graph.leaf_nodes.return_value = []
        MockExec.return_value.migration_plan.return_value = []
        response = healthz_view(rf.get("/api/healthz"))
    elapsed = time.monotonic() - started

    payload = json.loads(response.content)
    assert elapsed < 6
    assert response.status_code == 503
    assert "timed out" in payload["checks"]["cache"]["error"]
    assert payload["checks"]["db"]["ok"] is True
    assert payload["checks"]["migrations"]["ok"] is True
    assert payload["checks"]["config"]["ok"] is True


# ---------------------------------------------------------------------------
# Migrations check
# ---------------------------------------------------------------------------

class TestMigrationsCheck:
    def test_pending_returns_ok_false(self):
        fake = MagicMock()
        fake.app_label = "myapp"
        fake.name = "0002_add_field"
        with patch("django_core_micha.health.views.MigrationExecutor") as MockExec:
            MockExec.return_value.loader.graph.leaf_nodes.return_value = []
            MockExec.return_value.migration_plan.return_value = [(fake, False)]
            result = _check_migrations()

        assert result["ok"] is False
        assert "error" in result
        assert "1" in result["error"]
        assert "pending" in result["error"]
        assert "duration_ms" in result

    def test_no_pending_returns_ok_true(self):
        with patch("django_core_micha.health.views.MigrationExecutor") as MockExec:
            MockExec.return_value.loader.graph.leaf_nodes.return_value = []
            MockExec.return_value.migration_plan.return_value = []
            result = _check_migrations()

        assert result["ok"] is True
        assert "error" not in result

    def test_multiple_pending_count_in_error(self):
        fakes = [(MagicMock(), False), (MagicMock(), False), (MagicMock(), False)]
        with patch("django_core_micha.health.views.MigrationExecutor") as MockExec:
            MockExec.return_value.loader.graph.leaf_nodes.return_value = []
            MockExec.return_value.migration_plan.return_value = fakes
            result = _check_migrations()

        assert result["ok"] is False
        assert "3" in result["error"]

    def test_executor_exception_returns_ok_false_generic(self):
        with patch("django_core_micha.health.views.MigrationExecutor") as MockExec:
            MockExec.side_effect = Exception("FATAL: password auth failed for 'secret_user'")
            result = _check_migrations()

        assert result["ok"] is False
        assert result["error"] == "migrations check failed"
        # driver-level error must not leak
        assert "secret_user" not in json.dumps(result)
        assert "password" not in json.dumps(result)


# ---------------------------------------------------------------------------
# Config check
# ---------------------------------------------------------------------------

class TestConfigCheck:
    def test_resend_key_missing_fails(self):
        mock = _settings_mock(EMAIL_PROVIDER="resend", ANYMAIL={})
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is False
        assert "RESEND_API_KEY" in result["missing"]

    def test_resend_key_present_passes(self):
        mock = _settings_mock(EMAIL_PROVIDER="resend", ANYMAIL={"RESEND_API_KEY": "re_live_xyz"})
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is True
        assert "missing" not in result
        # value must not appear in serialised output
        assert "re_live_xyz" not in json.dumps(result)

    def test_non_resend_provider_skips_resend_check(self):
        mock = _settings_mock(EMAIL_PROVIDER="smtp")
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is True

    def test_no_email_provider_skips_resend_check(self):
        mock = _settings_mock(EMAIL_PROVIDER="")
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is True

    def test_social_client_id_missing_fails(self):
        mock = _settings_mock(
            EMAIL_PROVIDER="smtp",
            AUTH_METHODS={"social_login": True, "social_providers": ["google"]},
            SOCIALACCOUNT_PROVIDERS={"google": {"APP": {"client_id": ""}}},
        )
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is False
        assert "GOOGLE_CLIENT_ID" in result["missing"]

    def test_social_client_id_present_passes(self):
        mock = _settings_mock(
            EMAIL_PROVIDER="smtp",
            AUTH_METHODS={"social_login": True, "social_providers": ["google"]},
            SOCIALACCOUNT_PROVIDERS={"google": {"APP": {"client_id": "client-abc"}}},
        )
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is True
        assert "client-abc" not in json.dumps(result)

    def test_social_login_off_skips_provider_check(self):
        mock = _settings_mock(
            EMAIL_PROVIDER="smtp",
            AUTH_METHODS={"social_login": False, "social_providers": ["google"]},
            SOCIALACCOUNT_PROVIDERS={"google": {"APP": {"client_id": ""}}},
        )
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is True

    def test_missing_list_contains_names_not_values(self):
        """missing is a list[str] of key names — never a dict or value."""
        mock = _settings_mock(EMAIL_PROVIDER="resend", ANYMAIL={})
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert isinstance(result["missing"], list)
        assert all(isinstance(k, str) for k in result["missing"])
        assert "RESEND_API_KEY" in result["missing"]

    def test_multiple_providers_both_missing(self):
        mock = _settings_mock(
            EMAIL_PROVIDER="resend",
            ANYMAIL={},
            AUTH_METHODS={"social_login": True, "social_providers": ["google", "microsoft"]},
            SOCIALACCOUNT_PROVIDERS={
                "google": {"APP": {"client_id": ""}},
                "microsoft": {"APP": {"client_id": ""}},
            },
        )
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is False
        assert "RESEND_API_KEY" in result["missing"]
        assert "GOOGLE_CLIENT_ID" in result["missing"]
        assert "MICROSOFT_CLIENT_ID" in result["missing"]

    def test_exception_returns_ok_false_generic(self):
        # ANYMAIL=None → None.get(...) → AttributeError caught by outer except
        mock = _settings_mock(EMAIL_PROVIDER="resend", ANYMAIL=None)
        with patch("django_core_micha.health.views.settings", mock):
            result = _check_config()

        assert result["ok"] is False
        assert result["error"] == "config check failed"


# ---------------------------------------------------------------------------
# Version info
# ---------------------------------------------------------------------------

class TestVersionInfo:
    def test_sha_set(self):
        with patch.dict(os.environ, {"APP_GIT_SHA": "deadbeef01"}):
            assert _get_version_info() == "deadbeef01"

    def test_sha_unset_returns_none(self):
        env_without = {k: v for k, v in os.environ.items() if k != "APP_GIT_SHA"}
        with patch.dict(os.environ, env_without, clear=True):
            assert _get_version_info() is None

    def test_sha_empty_string_returns_none(self):
        with patch.dict(os.environ, {"APP_GIT_SHA": ""}):
            assert _get_version_info() is None

    @pytest.mark.django_db
    def test_version_appears_in_response(self, rf):
        with patch.dict(os.environ, {"APP_GIT_SHA": "abc123ff"}):
            response = _patched_view(rf)
        assert json.loads(response.content)["version"] == "abc123ff"

    @pytest.mark.django_db
    def test_version_null_in_response_when_unset(self, rf):
        env_without = {k: v for k, v in os.environ.items() if k != "APP_GIT_SHA"}
        with patch.dict(os.environ, env_without, clear=True):
            response = _patched_view(rf)
        assert json.loads(response.content)["version"] is None


# ---------------------------------------------------------------------------
# DB failure redaction
# ---------------------------------------------------------------------------

class TestDbFailureRedaction:
    @pytest.mark.django_db
    def test_db_error_generic_text_no_credential_leak(self):
        with patch("django_core_micha.health.views.connection") as mock_conn:
            mock_conn.cursor.side_effect = Exception(
                "FATAL: password authentication failed for user 'secret_db_user'"
            )
            result = _check_db()

        assert result["ok"] is False
        assert result["error"] == "database check failed"
        payload = json.dumps(result)
        assert "secret_db_user" not in payload
        assert "password authentication" not in payload
        assert "FATAL" not in payload
