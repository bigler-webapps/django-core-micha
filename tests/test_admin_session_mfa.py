"""Tests for MFA-authenticated admin sessions and allauth admin login wiring."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from django_core_micha.auth.admin_login import secure_admin_site
from django_core_micha.auth.admin_mfa_middleware import (
    AdminMfaRequiredMiddleware,
    session_used_mfa,
)

urlpatterns = []


def _install_authentication_records(monkeypatch):
    account = ModuleType("allauth.account")
    authentication = ModuleType("allauth.account.authentication")
    authentication.get_authentication_records = lambda request: request.session.get(
        "account_authentication_methods", []
    )
    monkeypatch.setitem(sys.modules, "allauth.account", account)
    monkeypatch.setitem(sys.modules, "allauth.account.authentication", authentication)


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([], False),
        ([{"method": "password", "type": "password"}], False),
        ([{"method": "social", "type": "oauth2"}], False),
        ([{"method": "mfa", "type": "totp"}], True),
        ([{"method": "mfa", "type": "webauthn"}], True),
        ([{"method": "mfa", "type": "recovery_codes"}], False),
        (None, False),
        ([{"method": "mfa"}], False),
    ],
)
def test_session_used_mfa_record_cases(monkeypatch, records, expected):
    _install_authentication_records(monkeypatch)
    request = RequestFactory().get("/admin/")
    request.session = {"account_authentication_methods": records}
    assert session_used_mfa(request) is expected


def test_session_used_mfa_malformed_records_fail_closed(monkeypatch):
    _install_authentication_records(monkeypatch)
    request = RequestFactory().get("/admin/")
    request.session = {"account_authentication_methods": [{"method": "mfa"}, "bad"]}
    assert session_used_mfa(request) is False


class _AuthenticatorType:
    RECOVERY_CODES = "recovery_codes"


def _install_authenticators(monkeypatch, enrolled=True):
    class QuerySet:
        def exclude(self, **kwargs):
            return self

        def exists(self):
            return enrolled

    class Manager:
        def filter(self, **kwargs):
            return QuerySet()

    models = ModuleType("allauth.mfa.models")
    models.Authenticator = SimpleNamespace(Type=_AuthenticatorType, objects=Manager())
    mfa = ModuleType("allauth.mfa")
    mfa.models = models
    allauth = ModuleType("allauth")
    allauth.mfa = mfa
    monkeypatch.setitem(sys.modules, "allauth", allauth)
    monkeypatch.setitem(sys.modules, "allauth.mfa", mfa)
    monkeypatch.setitem(sys.modules, "allauth.mfa.models", models)


def _middleware_request(*, superuser=False, staff=False, authenticated=True, records=None):
    request = RequestFactory().get("/admin/")
    request.user = SimpleNamespace(
        is_authenticated=authenticated,
        is_superuser=superuser,
        is_staff=staff,
    )
    request.session = {"account_authentication_methods": records or []}
    return request


@override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
@pytest.mark.parametrize("staff_role", [False, True], ids=["superuser", "staff"])
def test_enrolled_password_only_session_is_refused(monkeypatch, staff_role):
    _install_authenticators(monkeypatch, enrolled=True)
    _install_authentication_records(monkeypatch)
    request = _middleware_request(superuser=not staff_role, staff=staff_role)
    response = AdminMfaRequiredMiddleware(lambda _request: HttpResponse("ok"))(request)
    assert response.status_code == 403
    assert b"not authenticated with MFA" in response.content


@override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
@pytest.mark.parametrize("staff_role", [False, True], ids=["superuser", "staff"])
def test_enrolled_mfa_session_is_allowed(monkeypatch, staff_role):
    _install_authenticators(monkeypatch, enrolled=True)
    _install_authentication_records(monkeypatch)
    request = _middleware_request(
        superuser=not staff_role,
        staff=staff_role,
        records=[{"method": "mfa", "type": "totp"}],
    )
    response = AdminMfaRequiredMiddleware(lambda _request: HttpResponse("ok"))(request)
    assert response.status_code == 200


@override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
def test_mfa_session_without_enrolled_factor_is_refused(monkeypatch):
    _install_authenticators(monkeypatch, enrolled=False)
    _install_authentication_records(monkeypatch)
    request = _middleware_request(
        superuser=True, records=[{"method": "mfa", "type": "totp"}]
    )
    response = AdminMfaRequiredMiddleware(lambda _request: HttpResponse("ok"))(request)
    assert response.status_code == 403
    assert b"enrolled real MFA factor" in response.content


@override_settings(ADMIN_MFA_REQUIRED=True, IS_LOCAL=False)
def test_non_staff_non_superuser_and_anonymous_are_left_alone(monkeypatch):
    middleware = AdminMfaRequiredMiddleware(lambda _request: HttpResponse("ok"))
    for request in (
        _middleware_request(superuser=False, staff=False),
        _middleware_request(superuser=False, staff=False, authenticated=False),
    ):
        assert middleware(request).status_code == 200


@pytest.mark.parametrize(
    ("path", "local", "required"),
    [("/admin/", True, True), ("/admin/", False, False), ("/api/", False, True)],
)
def test_admin_mfa_bypasses_remain_unchanged(monkeypatch, path, local, required):
    _install_authenticators(monkeypatch, enrolled=False)
    request = _middleware_request(superuser=True)
    request.path = path
    request.path_info = path
    middleware = AdminMfaRequiredMiddleware(lambda _request: HttpResponse("ok"))
    with override_settings(ADMIN_MFA_REQUIRED=required, IS_LOCAL=local):
        assert middleware(request).status_code == 200


@override_settings(LOGIN_URL="/accounts/login/", ROOT_URLCONF=__name__)
@pytest.mark.parametrize("method", ["get", "post"])
def test_admin_login_redirects_anonymous_requests_without_password_auth(method):
    site = AdminSite()
    original_login = MagicMock(return_value=HttpResponse("admin login"))
    site.login = original_login
    secure_admin_site(site)
    request = getattr(RequestFactory(), method)(
        "/admin/login/?next=%2Fadmin%2F", {"username": "operator", "password": "secret"}
    ) if method == "post" else RequestFactory().get("/admin/login/?next=%2Fadmin%2F")
    request.user = SimpleNamespace(is_authenticated=False, is_anonymous=True)

    # Admin login no longer runs its own unthrottled password check. The
    # normal allauth login view owns password validation and rate limiting.
    with patch("django.contrib.auth.authenticate", side_effect=AssertionError("authenticate called")), patch(
        "django.contrib.auth.forms.AuthenticationForm.is_valid",
        side_effect=AssertionError("AuthenticationForm validated"),
    ), patch("allauth.account.decorators.get_next_redirect_url", return_value="/admin/"):
        response = site.login(request)

    assert response.status_code == 302
    parts = urlsplit(response["Location"])
    assert parts.path == "/accounts/login/"
    assert parse_qs(parts.query)["next"] == ["/admin/"]
    original_login.assert_not_called()


@override_settings(LOGIN_URL="/accounts/login/", ROOT_URLCONF=__name__)
def test_admin_login_allows_active_staff_and_rejects_non_staff():
    site = AdminSite()
    original_login = MagicMock(return_value=HttpResponse("admin login"))
    site.login = original_login
    secure_admin_site(site)
    request = RequestFactory().get("/admin/login/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_active=True)
    assert site.login(request) is original_login.return_value
    original_login.assert_called_once_with(request)

    request.user = SimpleNamespace(is_authenticated=True, is_staff=False, is_active=True)
    with pytest.raises(PermissionDenied):
        site.login(request)


def test_secure_admin_site_is_idempotent():
    site = AdminSite()
    secure_admin_site(site)
    wrapped_login = site.login
    secure_admin_site(site)
    assert site.login is wrapped_login


def test_script_prefix_does_not_hide_the_admin_from_the_gate(monkeypatch):
    request = RequestFactory().get("/admin/", SCRIPT_NAME="/portal")
    assert request.path == "/portal/admin/" and request.path_info == "/admin/"
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, is_staff=True, pk=1)
    with override_settings(IS_LOCAL=False, ADMIN_MFA_REQUIRED=True):
        assert AdminMfaRequiredMiddleware._needs_mfa_check(request) is True


def test_unknown_or_missing_mfa_types_fail_closed():
    for record in ({"method": "mfa", "type": "unknown"}, {"method": "mfa"}, {"method": "mfa", "type": ""}):
        request = RequestFactory().get("/admin/")
        request.session = {"account_authentication_methods": [record]}
        assert session_used_mfa(request) is False


def test_staff_with_an_mfa_session_but_no_enrolled_factor_is_refused(monkeypatch):
    from django_core_micha.auth import admin_mfa_middleware as module
    monkeypatch.setattr(AdminMfaRequiredMiddleware, "_has_mfa", staticmethod(lambda user: False))
    request = RequestFactory().get("/admin/")
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=False, is_staff=True, pk=2)
    request.session = {"account_authentication_methods": [{"method": "mfa", "type": "totp"}]}
    with override_settings(IS_LOCAL=False, ADMIN_MFA_REQUIRED=True):
        response = AdminMfaRequiredMiddleware(lambda r: HttpResponse("ok"))(request)
    assert response.status_code == 403 and module.session_used_mfa(request) is True


def test_ready_wires_the_default_admin_site_when_admin_and_allauth_are_installed(monkeypatch):
    from django.apps import apps
    from django.contrib import admin
    from django.contrib.admin.sites import AdminSite
    from django_core_micha.auth.admin_login import secure_default_admin_site
    fake_site = AdminSite()
    original_login = fake_site.login
    monkeypatch.setattr(admin, "site", fake_site)
    real = apps.is_installed
    monkeypatch.setattr(apps, "is_installed", lambda name: True if name in ("django.contrib.admin", "allauth.account") else real(name))
    assert secure_default_admin_site() is True
    assert fake_site.login is not original_login and getattr(fake_site.login, "_dcm_secure_admin_login", False) is True
    monkeypatch.setattr(apps, "is_installed", lambda name: False if name == "django.contrib.admin" else real(name))
    other = AdminSite(); other_login = other.login
    monkeypatch.setattr(admin, "site", other)
    assert secure_default_admin_site() is False and other.login == other_login


def test_a_non_admin_route_under_an_admin_script_prefix_is_not_gated():
    request = RequestFactory().get("/api/", SCRIPT_NAME="/admin")
    assert request.path == "/admin/api/" and request.path_info == "/api/"
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, is_staff=True, pk=1)
    with override_settings(IS_LOCAL=False, ADMIN_MFA_REQUIRED=True):
        assert AdminMfaRequiredMiddleware._needs_mfa_check(request) is False


def test_app_config_ready_calls_the_default_admin_wiring(monkeypatch):
    from django.apps import apps
    from django_core_micha.auth import admin_login, policy
    calls = []
    monkeypatch.setattr(admin_login, "secure_default_admin_site", lambda: calls.append("wired") or True)
    monkeypatch.setattr(policy, "_connect_auth_policy_cache_signals", lambda model: None)
    config = apps.get_app_config("django_core_micha_auth")
    config.ready()
    assert calls == ["wired"]


@override_settings(ROOT_URLCONF="tests.urls_backend_admin", IS_LOCAL=False, ADMIN_MFA_REQUIRED=True)
def test_an_admin_mounted_at_another_prefix_is_gated_too():
    user = SimpleNamespace(is_authenticated=True, is_superuser=False, is_staff=True, pk=3)
    for path in ("/backend/admin/", "/backend/admin/auth/user/", "/admin/anything/"):
        request = RequestFactory().get(path)
        request.user = user
        assert AdminMfaRequiredMiddleware._needs_mfa_check(request) is True, path
    api = RequestFactory().get("/api/")
    api.user = user
    assert AdminMfaRequiredMiddleware._needs_mfa_check(api) is False
    unknown = RequestFactory().get("/no/such/route/")
    unknown.user = user
    assert AdminMfaRequiredMiddleware._needs_mfa_check(unknown) is False


@override_settings(ROOT_URLCONF="tests.urls_backend_admin", IS_LOCAL=False, ADMIN_MFA_REQUIRED=True)
def test_a_password_only_session_is_refused_on_the_prefixed_admin(monkeypatch):
    monkeypatch.setattr(AdminMfaRequiredMiddleware, "_has_mfa", staticmethod(lambda user: True))
    request = RequestFactory().get("/backend/admin/")
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, is_staff=True, pk=4)
    request.session = {"account_authentication_methods": [{"method": "password"}]}
    response = AdminMfaRequiredMiddleware(lambda r: HttpResponse("ok"))(request)
    assert response.status_code == 403


@override_settings(ROOT_URLCONF="tests.urls_backend_admin", IS_LOCAL=False, ADMIN_MFA_REQUIRED=True)
def test_a_resolver_crash_fails_closed_for_staff_but_not_for_anonymous(monkeypatch):
    import django.urls as urls
    monkeypatch.setattr(urls, "resolve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    staff = RequestFactory().get("/somewhere/")
    staff.user = SimpleNamespace(is_authenticated=True, is_superuser=False, is_staff=True, pk=5)
    assert AdminMfaRequiredMiddleware._needs_mfa_check(staff) is True
    anonymous = RequestFactory().get("/somewhere/")
    anonymous.user = SimpleNamespace(is_authenticated=False, is_superuser=False, is_staff=False, pk=None)
    assert AdminMfaRequiredMiddleware._needs_mfa_check(anonymous) is False
