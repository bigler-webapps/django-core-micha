"""Unit tests for `django_core_micha.auth.admin_mfa_middleware` (S19).

These verify the gate logic of `AdminMfaRequiredMiddleware`:

- opt-out flag (`ADMIN_MFA_REQUIRED=False`) bypasses the gate
- non-`/admin/` URLs bypass
- local environments (`IS_LOCAL=True`) bypass
- unauthenticated requests bypass (Django admin's login flow handles them)
- non-superusers bypass (Django admin gates them itself)
- superuser without any real MFA factor is rejected with 403
- superuser with TOTP/WebAuthn passes
- superuser with **only** recovery codes is rejected (recovery codes
  alone do not satisfy the second-factor requirement)

The middleware imports `allauth.mfa.models.Authenticator` lazily inside
`_has_mfa`, so we inject a fake module into `sys.modules` rather than
pulling allauth into the test settings.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse, HttpResponseForbidden
from django.test import RequestFactory, override_settings

from django_core_micha.auth.admin_mfa_middleware import AdminMfaRequiredMiddleware


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _ok_response(_request):
    return HttpResponse("ok")


def _make_user(*, authenticated: bool = True, superuser: bool = True, pk: int = 1):
    """Return a duck-typed user with only the attributes the middleware reads."""
    return SimpleNamespace(
        is_authenticated=authenticated,
        is_superuser=superuser,
        pk=pk,
    )


def _install_fake_allauth(monkeypatch, authenticators):
    """Install a fake `allauth.mfa.models` module exposing `Authenticator`.

    `authenticators` is the list of objects the fake queryset will report.
    Each item must have a `.type` attribute matching one of the Type enum
    values (we use plain strings here — the middleware only checks equality
    via `.exclude(type=...)`).
    """

    class _Type:
        TOTP = "totp"
        WEBAUTHN = "webauthn"
        RECOVERY_CODES = "recovery_codes"

    class _QS:
        def __init__(self, items):
            self._items = list(items)

        def exclude(self, **kwargs):
            # Only the `type=...` exclude path is exercised by the middleware.
            excluded_type = kwargs.get("type")
            return _QS([a for a in self._items if a.type != excluded_type])

        def exists(self):
            return bool(self._items)

    class _Manager:
        def filter(self, **kwargs):
            # The middleware passes `user=user` — we ignore the kwargs because
            # the test controls which authenticators the manager returns.
            return _QS(authenticators)

    class _Authenticator:
        Type = _Type
        objects = _Manager()

    fake_models = ModuleType("allauth.mfa.models")
    fake_models.Authenticator = _Authenticator

    fake_mfa = ModuleType("allauth.mfa")
    fake_mfa.models = fake_models

    fake_allauth = ModuleType("allauth")
    fake_allauth.mfa = fake_mfa

    monkeypatch.setitem(sys.modules, "allauth", fake_allauth)
    monkeypatch.setitem(sys.modules, "allauth.mfa", fake_mfa)
    monkeypatch.setitem(sys.modules, "allauth.mfa.models", fake_models)
    return _Authenticator


def _auth(type_: str):
    """Construct a fake Authenticator row with only the `.type` attribute."""
    return SimpleNamespace(type=type_)


# --------------------------------------------------------------------------- #
# _needs_mfa_check: the no-MFA-import path
# --------------------------------------------------------------------------- #


class TestNeedsMfaCheck:
    def setup_method(self):
        self.rf = RequestFactory()
        self.mw = AdminMfaRequiredMiddleware(_ok_response)

    @override_settings(ADMIN_MFA_REQUIRED=False, IS_LOCAL=False)
    def test_opt_out_disables_check(self):
        request = self.rf.get("/admin/")
        request.user = _make_user()
        assert self.mw._needs_mfa_check(request) is False

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_non_admin_url_bypasses(self):
        request = self.rf.get("/api/whatever/")
        request.user = _make_user()
        assert self.mw._needs_mfa_check(request) is False

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=True)
    def test_local_env_bypasses(self):
        request = self.rf.get("/admin/")
        request.user = _make_user()
        assert self.mw._needs_mfa_check(request) is False

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_unauthenticated_bypasses(self):
        request = self.rf.get("/admin/login/")
        request.user = _make_user(authenticated=False)
        assert self.mw._needs_mfa_check(request) is False

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_missing_user_bypasses(self):
        # Defensive: some test request objects don't set `.user`.
        request = self.rf.get("/admin/")
        # Intentionally don't set request.user — middleware uses `getattr`.
        assert self.mw._needs_mfa_check(request) is False

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_non_superuser_bypasses(self):
        request = self.rf.get("/admin/")
        request.user = _make_user(superuser=False)
        assert self.mw._needs_mfa_check(request) is False

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_authenticated_superuser_on_admin_triggers_check(self):
        request = self.rf.get("/admin/")
        request.user = _make_user()
        assert self.mw._needs_mfa_check(request) is True

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_admin_subpath_triggers_check(self):
        # `/admin/auth/user/` should be treated like `/admin/` itself.
        request = self.rf.get("/admin/auth/user/")
        request.user = _make_user()
        assert self.mw._needs_mfa_check(request) is True


# --------------------------------------------------------------------------- #
# _has_mfa: factor classification (TOTP/WebAuthn pass, recovery-only fails)
# --------------------------------------------------------------------------- #


class TestHasMfa:
    def test_no_authenticators_returns_false(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[])
        user = _make_user()
        assert AdminMfaRequiredMiddleware._has_mfa(user) is False

    def test_totp_returns_true(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[_auth("totp")])
        user = _make_user()
        assert AdminMfaRequiredMiddleware._has_mfa(user) is True

    def test_webauthn_returns_true(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[_auth("webauthn")])
        user = _make_user()
        assert AdminMfaRequiredMiddleware._has_mfa(user) is True

    def test_recovery_codes_only_returns_false(self, monkeypatch):
        """Critical security property: a superuser whose only Authenticator
        row is RECOVERY_CODES must NOT pass the admin gate. Recovery codes
        are a fallback factor, not a real second factor.
        """
        _install_fake_allauth(
            monkeypatch, authenticators=[_auth("recovery_codes")]
        )
        user = _make_user()
        assert AdminMfaRequiredMiddleware._has_mfa(user) is False

    def test_totp_plus_recovery_codes_returns_true(self, monkeypatch):
        """Mixed case — once a real factor is present, recovery codes
        living alongside it don't disqualify the user.
        """
        _install_fake_allauth(
            monkeypatch,
            authenticators=[_auth("totp"), _auth("recovery_codes")],
        )
        user = _make_user()
        assert AdminMfaRequiredMiddleware._has_mfa(user) is True


# --------------------------------------------------------------------------- #
# __call__: end-to-end gate behaviour
# --------------------------------------------------------------------------- #


class TestCallGate:
    def setup_method(self):
        self.rf = RequestFactory()
        # Wrap the downstream view in a mock so we can assert pass-through.
        self.downstream = MagicMock(side_effect=_ok_response)
        self.mw = AdminMfaRequiredMiddleware(self.downstream)

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_superuser_without_mfa_gets_403(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[])
        request = self.rf.get("/admin/")
        request.user = _make_user()

        response = self.mw(request)

        assert isinstance(response, HttpResponseForbidden)
        assert response.status_code == 403
        self.downstream.assert_not_called()

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_superuser_with_recovery_codes_only_gets_403(self, monkeypatch):
        _install_fake_allauth(
            monkeypatch, authenticators=[_auth("recovery_codes")]
        )
        request = self.rf.get("/admin/")
        request.user = _make_user()

        response = self.mw(request)

        assert response.status_code == 403
        self.downstream.assert_not_called()

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_superuser_with_totp_passes_through(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[_auth("totp")])
        request = self.rf.get("/admin/")
        request.user = _make_user()

        response = self.mw(request)

        assert response.status_code == 200
        self.downstream.assert_called_once_with(request)

    @override_settings(ADMIN_MFA_REQUIRED=False, IS_LOCAL=False)
    def test_opt_out_passes_through_without_consulting_mfa(self, monkeypatch):
        # Install fake with NO authenticators — would normally block, but
        # opt-out short-circuits before `_has_mfa` is consulted.
        _install_fake_allauth(monkeypatch, authenticators=[])
        request = self.rf.get("/admin/")
        request.user = _make_user()

        response = self.mw(request)

        assert response.status_code == 200
        self.downstream.assert_called_once_with(request)

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=True)
    def test_local_env_passes_through(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[])
        request = self.rf.get("/admin/")
        request.user = _make_user()

        response = self.mw(request)

        assert response.status_code == 200
        self.downstream.assert_called_once_with(request)

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_unauthenticated_admin_login_passes_through(self, monkeypatch):
        """Bootstrap path: `/admin/login/` must reach Django so the user can
        actually log in. Only AFTER login does the gate engage.
        """
        _install_fake_allauth(monkeypatch, authenticators=[])
        request = self.rf.get("/admin/login/")
        request.user = _make_user(authenticated=False)

        response = self.mw(request)

        assert response.status_code == 200
        self.downstream.assert_called_once_with(request)

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_non_superuser_passes_through(self, monkeypatch):
        """Django admin already rejects non-superusers — the gate must not
        layer on a confusing 403 before that check runs.
        """
        _install_fake_allauth(monkeypatch, authenticators=[])
        request = self.rf.get("/admin/")
        request.user = _make_user(superuser=False)

        response = self.mw(request)

        assert response.status_code == 200
        self.downstream.assert_called_once_with(request)

    @override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
    def test_non_admin_url_passes_through(self, monkeypatch):
        _install_fake_allauth(monkeypatch, authenticators=[])
        request = self.rf.get("/api/foo/")
        request.user = _make_user()

        response = self.mw(request)

        assert response.status_code == 200
        self.downstream.assert_called_once_with(request)
