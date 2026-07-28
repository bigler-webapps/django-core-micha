"""Turnstile bot-check coverage for self-service registration."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from django.test import override_settings
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from django_core_micha.auth import turnstile
from django_core_micha.auth.methods import get_auth_methods
from django_core_micha.auth.policy import (
    AUTH_FACTOR_SINGLE,
    SIGNUP_MODE_ACCESS_CODE,
    SIGNUP_MODE_EMAIL_DOMAIN,
    SIGNUP_MODE_OPEN,
    SIGNUP_MODE_QR,
    RegistrationPolicyState,
    create_signup_context_token,
)
from django_core_micha.auth.views import BaseUserViewSet


class _SiteverifyResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _policy(**overrides):
    values = {
        "allow_admin_invite": True,
        "allow_self_signup_access_code": False,
        "allow_self_signup_open": True,
        "allow_self_signup_email_domain": True,
        "allow_self_signup_qr": False,
        "allowed_email_domains": ["example.com"],
        "required_auth_factor_count": AUTH_FACTOR_SINGLE,
        "admin_required_auth_factor_count": AUTH_FACTOR_SINGLE,
        "signup_qr_expiry_days": 90,
        "access_code_single_use": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _register(payload):
    request = APIRequestFactory().post(
        "/users/register-request/", payload, format="json", HTTP_HOST="app.example.com"
    )
    response = BaseUserViewSet.as_view(
        {"post": "register_request"}, permission_classes=[AllowAny]
    )(request)
    response.render()
    return response


@pytest.mark.parametrize("mode", [SIGNUP_MODE_OPEN, SIGNUP_MODE_EMAIL_DOMAIN])
@override_settings(TURNSTILE_SECRET_KEY="secret", ALLOWED_HOSTS=["app.example.com"])
def test_open_signup_modes_require_a_valid_turnstile_token(monkeypatch, mode):
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: _policy())
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "django_core_micha.auth.views.verify_turnstile_token", lambda *args, **kwargs: (True, "")
    )

    response = _register(
        {"email": "new@example.com", "mode": mode, "turnstile_token": "valid-token"}
    )

    assert response.status_code == 201


@pytest.mark.parametrize("mode", [SIGNUP_MODE_OPEN, SIGNUP_MODE_EMAIL_DOMAIN])
@override_settings(TURNSTILE_SECRET_KEY="secret", ALLOWED_HOSTS=["app.example.com"])
def test_open_signup_modes_reject_failed_turnstile(monkeypatch, mode):
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: _policy())
    monkeypatch.setattr(
        "django_core_micha.auth.views.verify_turnstile_token", lambda *args, **kwargs: (False, "bad")
    )

    response = _register({"email": "new@example.com", "mode": mode})

    assert response.status_code == 400
    assert str(response.data["turnstile_token"]) == "Bot verification failed."


@override_settings(TURNSTILE_SECRET_KEY="secret", ALLOWED_HOSTS=["*"])
def test_open_signup_rejects_when_no_exact_allowed_hostname_exists(monkeypatch):
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: _policy())
    monkeypatch.setattr(
        "django_core_micha.auth.views.verify_turnstile_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )

    response = _register(
        {"email": "new@example.com", "mode": SIGNUP_MODE_OPEN, "turnstile_token": "token"}
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("payload", "exception"),
    [
        ({"success": False}, None),
        ({"success": True, "hostname": "other.example.com"}, None),
        (None, TimeoutError()),
    ],
)
@override_settings(TURNSTILE_SECRET_KEY="secret", ALLOWED_HOSTS=["app.example.com"])
def test_open_signup_fails_closed_when_siteverify_fails(monkeypatch, payload, exception):
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: _policy())

    if exception:
        monkeypatch.setattr(
            turnstile,
            "urlopen",
            lambda *args, **kwargs: (_ for _ in ()).throw(exception),
        )
    else:
        monkeypatch.setattr(
            turnstile, "urlopen", lambda *args, **kwargs: _SiteverifyResponse(payload)
        )

    response = _register(
        {"email": "new@example.com", "mode": SIGNUP_MODE_OPEN, "turnstile_token": "token"}
    )

    assert response.status_code == 400


@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_secret_unset_keeps_all_self_signup_modes_as_a_turnstile_noop(monkeypatch):
    policy = _policy(allow_self_signup_access_code=True, allow_self_signup_qr=True)
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: policy)
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "django_core_micha.auth.views.validate_access_code_or_error", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "django_core_micha.auth.views.verify_turnstile_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )
    qr_token, _expires_at = create_signup_context_token(policy=policy)

    payloads = (
        {"email": "new@example.com", "mode": SIGNUP_MODE_OPEN},
        {"email": "new@example.com", "mode": SIGNUP_MODE_EMAIL_DOMAIN},
        {
            "email": "new@example.com",
            "mode": SIGNUP_MODE_ACCESS_CODE,
            "access_code": "CODE",
        },
        {
            "email": "new@example.com",
            "mode": SIGNUP_MODE_QR,
            "registration_context_token": qr_token,
        },
    )

    for payload in payloads:
        assert _register(payload).status_code == 201


@override_settings(TURNSTILE_SECRET_KEY="secret", ALLOWED_HOSTS=["app.example.com"])
def test_access_code_and_qr_signup_do_not_require_turnstile(monkeypatch):
    policy = _policy(allow_self_signup_access_code=True, allow_self_signup_qr=True)
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: policy)
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "django_core_micha.auth.views.validate_access_code_or_error", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "django_core_micha.auth.views.verify_turnstile_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )
    qr_token, _expires_at = create_signup_context_token(policy=policy)

    assert _register(
        {"email": "new@example.com", "mode": SIGNUP_MODE_ACCESS_CODE, "access_code": "CODE"}
    ).status_code == 201
    assert _register(
        {
            "email": "new@example.com",
            "mode": SIGNUP_MODE_QR,
            "registration_context_token": qr_token,
        }
    ).status_code == 201


@override_settings(TURNSTILE_SECRET_KEY="", TURNSTILE_SITE_KEY="site-key")
def test_auth_methods_omits_turnstile_site_key_without_secret():
    assert "turnstile_site_key" not in get_auth_methods()


@override_settings(TURNSTILE_SECRET_KEY="secret", TURNSTILE_SITE_KEY="site-key")
def test_auth_methods_exposes_turnstile_site_key_only_with_secret():
    assert get_auth_methods()["turnstile_site_key"] == "site-key"


@override_settings(TURNSTILE_SECRET_KEY="secret")
def test_verify_turnstile_token_accepts_success_for_an_allowed_hostname(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = parse_qs(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _SiteverifyResponse({"success": True, "hostname": "app.example.com"})

    monkeypatch.setattr(turnstile, "urlopen", fake_urlopen)

    assert turnstile.verify_turnstile_token(
        "token", remoteip="203.0.113.1", allowed_hostnames=("app.example.com",)
    ) == (True, "")
    assert captured == {
        "body": {"secret": ["secret"], "response": ["token"], "remoteip": ["203.0.113.1"]},
        "timeout": 5,
    }


@pytest.mark.parametrize(
    ("token", "payload", "allowed_hostnames"),
    [
        ("", {"success": True, "hostname": "app.example.com"}, ("app.example.com",)),
        ("token", {"success": False}, ("app.example.com",)),
        ("token", {"success": True, "hostname": "other.example.com"}, ("app.example.com",)),
    ],
)
def test_verify_turnstile_token_fails_closed_for_missing_invalid_or_wrong_host(
    monkeypatch, token, payload, allowed_hostnames
):
    monkeypatch.setattr(turnstile, "urlopen", lambda *args, **kwargs: _SiteverifyResponse(payload))

    assert turnstile.verify_turnstile_token(token, allowed_hostnames=allowed_hostnames)[0] is False


def test_verify_turnstile_token_fails_closed_when_siteverify_is_unavailable(monkeypatch):
    monkeypatch.setattr(turnstile, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))

    assert turnstile.verify_turnstile_token("token")[0] is False
