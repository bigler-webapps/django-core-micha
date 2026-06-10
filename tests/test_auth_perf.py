"""Performance / query-count tests for auth helpers.

Tests:
  1. Memo correctness: _admin_policy_satisfied calls is_user_security_sufficient only once.
  2. Memo invalidation: set_security_level drops _dcm_admin_policy_cache.
  3. Single authenticator query in get_user_security_state.
  4. End-to-end bound for BaseUserSerializer (skipped if not importable in this env).

Note: `security.py` imports `recovery.RecoveryRequest` (a Django Model) which needs
the app in INSTALLED_APPS. We inject a fake stub into sys.modules before any
django_core_micha.auth imports so the tests run without the full app installed.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory, override_settings

# ---------------------------------------------------------------------------
# Inject fake recovery module so security.py / permissions.py can be imported
# without django_core_micha being in INSTALLED_APPS.
# ---------------------------------------------------------------------------
if "django_core_micha.auth.recovery" not in sys.modules:
    _fake_recovery = ModuleType("django_core_micha.auth.recovery")
    _fake_recovery.RecoveryRequest = type("RecoveryRequest", (), {})
    sys.modules["django_core_micha.auth.recovery"] = _fake_recovery

# ---------------------------------------------------------------------------
# Settings constants
# ---------------------------------------------------------------------------
SECURITY_LEVELS_SETTING = {"basic": 0, "strong": 1}
SECURITY_DEFAULT_LEVEL_SETTING = "basic"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin_user(pk: int = 1):
    """Duck-typed user: is_superuser → is_subject_to_admin_auth_policy returns True."""
    return SimpleNamespace(
        pk=pk,
        is_authenticated=True,
        is_superuser=True,
    )


# ---------------------------------------------------------------------------
# Test 1: Memo correctness
# ---------------------------------------------------------------------------

@override_settings(
    SECURITY_LEVELS=SECURITY_LEVELS_SETTING,
    SECURITY_DEFAULT_LEVEL=SECURITY_DEFAULT_LEVEL_SETTING,
)
def test_admin_policy_memo_calls_backend_once():
    """_admin_policy_satisfied must hit is_user_security_sufficient only once per request."""
    from django_core_micha.auth.permissions import _admin_policy_satisfied

    req = RequestFactory().get("/")
    req.session = {}
    user = _admin_user()

    with patch(
        "django_core_micha.auth.permissions.is_subject_to_admin_auth_policy",
        return_value=True,
    ), patch(
        "django_core_micha.auth.permissions.is_user_security_sufficient",
        return_value=True,
    ) as mock_fn:
        result1 = _admin_policy_satisfied(user, request=req)
        result2 = _admin_policy_satisfied(user, request=req)

    assert mock_fn.call_count == 1, (
        f"Expected is_user_security_sufficient called once, got {mock_fn.call_count}"
    )
    assert result1 == result2 == True


# ---------------------------------------------------------------------------
# Test 2: Memo invalidation
# ---------------------------------------------------------------------------

@override_settings(
    SECURITY_LEVELS=SECURITY_LEVELS_SETTING,
    SECURITY_DEFAULT_LEVEL=SECURITY_DEFAULT_LEVEL_SETTING,
)
def test_set_security_level_invalidates_memo():
    """set_security_level must delete _dcm_admin_policy_cache from the request."""
    from django_core_micha.auth.permissions import _admin_policy_satisfied
    from django_core_micha.auth.security import set_security_level

    req = RequestFactory().get("/")
    req.session = {}
    user = _admin_user()

    with patch(
        "django_core_micha.auth.permissions.is_subject_to_admin_auth_policy",
        return_value=True,
    ), patch(
        "django_core_micha.auth.permissions.is_user_security_sufficient",
        return_value=True,
    ):
        _admin_policy_satisfied(user, request=req)  # populate cache

    assert hasattr(req, "_dcm_admin_policy_cache"), "Cache should exist after first call"

    req.user = user  # set_security_level checks request.user.is_authenticated
    set_security_level(req, "strong")

    assert not hasattr(req, "_dcm_admin_policy_cache"), (
        "Cache must be cleared after set_security_level"
    )


# ---------------------------------------------------------------------------
# Test 3: Single authenticator query
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(
    SECURITY_LEVELS=SECURITY_LEVELS_SETTING,
    SECURITY_DEFAULT_LEVEL=SECURITY_DEFAULT_LEVEL_SETTING,
)
def test_get_user_security_state_single_authenticator_query():
    """get_user_security_state must issue exactly ONE query to mfa_authenticator."""
    from django.contrib.auth import get_user_model
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    from django_core_micha.auth.security import get_user_security_state

    User = get_user_model()
    user = User.objects.create_user(
        username="perf_test_user",
        email="perf@example.com",
        password="testpassword123",
    )

    with CaptureQueriesContext(connection) as ctx:
        get_user_security_state(user)

    authenticator_queries = [
        q for q in ctx.captured_queries
        if "mfa_authenticator" in q["sql"].lower()
    ]
    assert len(authenticator_queries) == 1, (
        f"Expected exactly 1 authenticator query, got {len(authenticator_queries)}. "
        f"All queries: {[q['sql'] for q in ctx.captured_queries]}"
    )


# ---------------------------------------------------------------------------
# Test 4: End-to-end bound for BaseUserSerializer
# ---------------------------------------------------------------------------

def _try_import_base_user_serializer():
    try:
        from django_core_micha.auth.serializers import BaseUserSerializer
        return BaseUserSerializer
    except Exception:
        return None


@pytest.mark.django_db
@override_settings(
    SECURITY_LEVELS=SECURITY_LEVELS_SETTING,
    SECURITY_DEFAULT_LEVEL=SECURITY_DEFAULT_LEVEL_SETTING,
)
def test_base_user_serializer_memo_fires_policy_check_once():
    """End-to-end: full BaseUserSerializer serialization must call
    is_user_security_sufficient at most once, regardless of how many
    ui_permissions helpers invoke _admin_policy_satisfied.
    """
    BaseUserSerializer = _try_import_base_user_serializer()
    if BaseUserSerializer is None:
        pytest.skip("BaseUserSerializer not available in this repo")

    from django.contrib.auth import get_user_model

    User = get_user_model()
    admin_user = User.objects.create_user(
        username="admin_ser_user",
        email="admin_ser@example.com",
        password="testpassword123",
        is_superuser=True,
    )

    req = RequestFactory().get("/")
    req.user = admin_user
    req.session = {}

    with patch(
        "django_core_micha.auth.permissions.is_subject_to_admin_auth_policy",
        return_value=True,
    ), patch(
        "django_core_micha.auth.permissions.is_user_security_sufficient",
        return_value=True,
    ) as mock_fn:
        serializer = BaseUserSerializer(admin_user, context={"request": req})
        _ = serializer.data

    assert mock_fn.call_count <= 1, (
        f"is_user_security_sufficient called {mock_fn.call_count} times — "
        "memo is not working end-to-end"
    )
