"""Dispatch-level coverage for the shared registration target throttle."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from django_core_micha.auth.policy import (
    SIGNUP_MODE_OPEN,
    create_pending_registration_token,
)
from django_core_micha.auth.throttles import (
    CoreAnonRateThrottle,
    PerAccessCodeScopedRateThrottle,
    PerEmailScopedRateThrottle,
)
from django_core_micha.auth.views import BaseUserViewSet


def _policy():
    return SimpleNamespace(
        allow_admin_invite=True,
        allow_self_signup_access_code=False,
        allow_self_signup_open=True,
        allow_self_signup_email_domain=False,
        allow_self_signup_qr=False,
        allowed_email_domains=[],
        required_auth_factor_count=1,
        admin_required_auth_factor_count=1,
        signup_qr_expiry_days=90,
        access_code_single_use=False,
    )


def _dispatch(action, payload, client_ip="192.0.2.1"):
    request = APIRequestFactory().post(
        f"/users/{action.replace('_', '-')}/",
        payload,
        format="json",
        HTTP_HOST="app.example.com",
        HTTP_CF_CONNECTING_IP=client_ip,
    )
    response = BaseUserViewSet.as_view(
        {"post": action}, permission_classes=[AllowAny]
    )(request)
    response.render()
    return response


@pytest.fixture
def registration_setup(monkeypatch):
    cache.clear()
    monkeypatch.setattr(BaseUserViewSet, "throttle_classes", [CoreAnonRateThrottle])
    monkeypatch.setattr(BaseUserViewSet, "get_auth_policy", lambda self: _policy())
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email",
        lambda **kwargs: None,
    )
    rates = {
        "anon": "100/hour",
        "email_register_request": "2/hour",
        "email_password_reset": "2/hour",
        "email_recovery_login": "2/hour",
        "email_mfa_support_help": "2/hour",
        "access_code_probe": "2/hour",
    }
    for throttle_class in (
        CoreAnonRateThrottle,
        PerAccessCodeScopedRateThrottle,
        PerEmailScopedRateThrottle,
    ):
        monkeypatch.setattr(throttle_class, "THROTTLE_RATES", rates)


@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_registration_target_quota_is_shared_across_clients_and_rejection_sends_no_mail(
    monkeypatch, registration_setup
):
    sent = []
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email",
        lambda *, email, url: sent.append(email),
    )

    first = _dispatch(
        "register_request", {"email": "Alice@example.com", "mode": SIGNUP_MODE_OPEN},
        "192.0.2.10",
    )
    second = _dispatch(
        "register_request", {"email": "ALICE@EXAMPLE.COM", "mode": SIGNUP_MODE_OPEN},
        "192.0.2.11",
    )
    assert sent == ["alice@example.com", "alice@example.com"]
    rejected = _dispatch(
        "register_request", {"email": "alice@example.com", "mode": SIGNUP_MODE_OPEN},
        "192.0.2.12",
    )
    assert len(sent) == 2
    separate_address = _dispatch(
        "register_request", {"email": "other@example.com", "mode": SIGNUP_MODE_OPEN},
        "192.0.2.13",
    )

    assert [first.status_code, second.status_code, rejected.status_code, separate_address.status_code] == [
        201,
        201,
        429,
        201,
    ]
    assert sent == [
        "alice@example.com", "alice@example.com", "other@example.com",
    ]


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_registration_response_shape_and_confirmation_preserve_existing_account(
    registration_setup,
):
    User = get_user_model()
    existing = User.objects.create_user(
        username="legacy-account-name",
        email="existing@example.com",
        password="Previous-Password-927!",
    )

    new_response = _dispatch(
        "register_request", {"email": "new@example.com", "mode": SIGNUP_MODE_OPEN}
    )
    existing_response = _dispatch(
        "register_request", {"email": "existing@example.com", "mode": SIGNUP_MODE_OPEN},
        "192.0.2.2",
    )

    assert new_response.status_code == existing_response.status_code == 201
    assert new_response.data["code"] == existing_response.data["code"] == "Auth.INVITE_SENT"
    assert new_response.data["mode"] == existing_response.data["mode"] == SIGNUP_MODE_OPEN

    token = create_pending_registration_token(
        email="existing@example.com", mode=SIGNUP_MODE_OPEN
    )
    confirmed = _dispatch(
        "register_confirm", {"token": token, "password": "Valid-Confirm-Password-927!"}
    )

    existing.refresh_from_db()
    assert confirmed.status_code == 201
    assert confirmed.data == {
        "code": "Auth.REGISTRATION_CONFIRMED",
        "email": "existing@example.com",
    }
    assert existing.username == "legacy-account-name"
    assert existing.check_password("Previous-Password-927!")
    assert User.objects.filter(email__iexact="existing@example.com").count() == 1
