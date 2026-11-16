"""Security and transaction coverage for opt-in registration extensions."""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import override_settings
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from django_core_micha.auth.policy import (
    SIGNUP_MODE_ACCESS_CODE,
    SIGNUP_MODE_OPEN,
    create_signup_context_token,
    create_pending_registration_token,
    decode_pending_registration_token,
)
from django_core_micha.auth.serializers import RegistrationRequestSerializer
from django_core_micha.auth.views import BaseUserViewSet
from django_core_micha.invitations.models import AccessCode


def _policy(*, access_code=False, single_use=False):
    return SimpleNamespace(
        allow_admin_invite=True,
        allow_self_signup_access_code=access_code,
        allow_self_signup_open=not access_code,
        allow_self_signup_email_domain=False,
        allow_self_signup_qr=False,
        allowed_email_domains=[],
        required_auth_factor_count=1,
        admin_required_auth_factor_count=1,
        signup_qr_expiry_days=90,
        access_code_single_use=single_use,
    )


class ExtensionRequestSerializer(RegistrationRequestSerializer):
    application_value = serializers.CharField(
        required=False, allow_blank=False, write_only=True
    )


class ExtensionViewSet(BaseUserViewSet):
    prepared = []
    applied = []
    fail_application = False
    policy = _policy()

    def get_auth_policy(self):
        return self.policy

    def get_registration_request_serializer(self, *, data):
        return ExtensionRequestSerializer(data=data)

    def prepare_registration_binding(self, *, email, mode, binding_ref, input_data):
        value_hash = hashlib.sha256(
            input_data["application_value"].encode("utf-8")
        ).hexdigest()
        self.prepared.append((email, mode, binding_ref, value_hash))

    def apply_registration_binding(self, user, *, binding_ref):
        record = next(item for item in self.prepared if item[2] == binding_ref)
        if record[0] != user.email.lower():
            raise ValueError("binding mismatch")
        if self.fail_application:
            AccessCode.objects.create(code="extension-rollback-write")
            raise RuntimeError("consumer write failed")
        self.applied.append(binding_ref)


class DefaultViewSet(BaseUserViewSet):
    def get_auth_policy(self):
        return _policy()


def _dispatch(viewset, action, payload):
    request = APIRequestFactory().post(
        f"/users/{action.replace('_', '-')}/",
        payload,
        format="json",
        HTTP_HOST="app.example.com",
    )
    response = viewset.as_view({"post": action}, **getattr(viewset, action).kwargs)(request)
    response.render()
    return response


@pytest.fixture(autouse=True)
def _reset_extension(monkeypatch):
    ExtensionViewSet.prepared = []
    ExtensionViewSet.applied = []
    ExtensionViewSet.fail_application = False
    ExtensionViewSet.policy = _policy()
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email",
        lambda **kwargs: None,
    )


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_default_request_contract_and_opt_in_extension_validation():
    default = _dispatch(
        DefaultViewSet,
        "register_request",
        {"email": "plain@example.com", "mode": SIGNUP_MODE_OPEN},
    )
    invalid = _dispatch(
        ExtensionViewSet,
        "register_request",
        {
            "email": "value@example.com",
            "mode": SIGNUP_MODE_OPEN,
            "application_value": "",
        },
    )

    assert default.status_code == 201
    assert default.data == {
        "code": "Auth.INVITE_SENT",
        "email": "plain@example.com",
        "mode": SIGNUP_MODE_OPEN,
    }
    assert invalid.status_code == 400
    assert ExtensionViewSet.prepared == []


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="secret", ALLOWED_HOSTS=["app.example.com"])
def test_anti_abuse_runs_before_extension_preparation(monkeypatch):
    monkeypatch.setattr(
        "django_core_micha.auth.views.verify_turnstile_token",
        lambda *args, **kwargs: (False, "invalid"),
    )
    response = _dispatch(
        ExtensionViewSet,
        "register_request",
        {
            "email": "bot@example.com",
            "mode": SIGNUP_MODE_OPEN,
            "application_value": "sensitive-value",
            "turnstile_token": "bad",
        },
    )
    assert response.status_code == 400
    assert ExtensionViewSet.prepared == []


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
@pytest.mark.parametrize("case", ["disabled", "access_code", "domain", "qr"])
def test_policy_rejection_precedes_extension_preparation(case):
    policy = _policy()
    payload = {"email": "user@example.com", "application_value": "grant-value"}
    if case == "disabled":
        policy.allow_self_signup_open = False
        payload["mode"] = SIGNUP_MODE_OPEN
    elif case == "access_code":
        policy.allow_self_signup_access_code = True
        payload.update(mode=SIGNUP_MODE_ACCESS_CODE, access_code="invalid")
    elif case == "domain":
        policy.allow_self_signup_email_domain = True
        policy.allowed_email_domains = ["other.example"]
        payload["mode"] = "self_signup_email_domain"
    else:
        policy.allow_self_signup_qr = True
        payload.update(mode="self_signup_qr", registration_context_token="invalid")
    ExtensionViewSet.policy = policy
    response = _dispatch(ExtensionViewSet, "register_request", payload)
    assert response.status_code == 400
    assert ExtensionViewSet.prepared == []


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_extension_registration_obeys_email_throttle_before_preparation(monkeypatch):
    from django_core_micha.auth.throttles import PerEmailScopedRateThrottle

    cache.clear()
    monkeypatch.setattr(PerEmailScopedRateThrottle, "THROTTLE_RATES", {"email_register_request": "1/hour"})
    payload = {"email": "limited@example.com", "mode": SIGNUP_MODE_OPEN, "application_value": "grant"}
    first = _dispatch(ExtensionViewSet, "register_request", payload)
    second = _dispatch(ExtensionViewSet, "register_request", payload)
    assert (first.status_code, second.status_code) == (201, 429)
    assert len(ExtensionViewSet.prepared) == 1
    cache.clear()


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
@pytest.mark.parametrize("action", ["register_request", "register_confirm"])
def test_extension_actions_require_csrf_for_authenticated_session(action):
    user = get_user_model().objects.create_user(username="signed-in@example.com", email="signed-in@example.com", password="test-password")
    request = APIRequestFactory(enforce_csrf_checks=True).post(
        "/users/register-request/",
        {"email": "new@example.com", "mode": SIGNUP_MODE_OPEN, "application_value": "grant"},
        format="json", HTTP_HOST="app.example.com",
    )
    request.user = user
    response = ExtensionViewSet.as_view({"post": action}, **getattr(ExtensionViewSet, action).kwargs)(request)
    assert response.status_code == 403
    assert ExtensionViewSet.prepared == []
    assert ExtensionViewSet.applied == []


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_confirmation_obeys_configured_anonymous_throttle(monkeypatch):
    from django_core_micha.auth.throttles import CoreAnonRateThrottle
    cache.clear()
    monkeypatch.setattr(CoreAnonRateThrottle, "THROTTLE_RATES", {"anon": "1/hour"})
    monkeypatch.setattr(ExtensionViewSet, "throttle_classes", [CoreAnonRateThrottle])
    payload = {"token": "invalid", "password": "Valid-Confirm-Password-927!"}
    first = _dispatch(ExtensionViewSet, "register_confirm", payload)
    second = _dispatch(ExtensionViewSet, "register_confirm", payload)
    assert (first.status_code, second.status_code) == (400, 429)
    assert ExtensionViewSet.applied == []
    cache.clear()


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_only_opaque_reference_enters_confirmation_url_and_signed_payload(caplog, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "django_core_micha.auth.views.send_pending_registration_email",
        lambda *, email, url: sent.append(url),
    )
    raw_value = "private-registration-value-48291"
    with caplog.at_level(logging.DEBUG):
        response = _dispatch(
            ExtensionViewSet,
            "register_request",
            {
                "email": "linked@example.com",
                "mode": SIGNUP_MODE_OPEN,
                "application_value": raw_value,
            },
        )

    assert response.status_code == 201
    assert len(sent) == 1
    assert raw_value not in sent[0]
    token = sent[0].split("token=", 1)[1]
    payload = decode_pending_registration_token(token)
    assert payload["registration_binding_ref"] == ExtensionViewSet.prepared[0][2]
    assert raw_value not in token
    assert raw_value not in repr(payload)
    assert raw_value not in caplog.text
    assert ExtensionViewSet.prepared[0][3] == hashlib.sha256(raw_value.encode()).hexdigest()
    assert ExtensionViewSet.applied == []


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_confirmation_in_another_browser_applies_once_and_existing_user_is_private():
    _dispatch(
        ExtensionViewSet,
        "register_request",
        {
            "email": "confirm@example.com",
            "mode": SIGNUP_MODE_OPEN,
            "application_value": "hashed-by-consumer",
        },
    )
    token = create_pending_registration_token(
        email="confirm@example.com",
        mode=SIGNUP_MODE_OPEN,
        registration_binding_ref=ExtensionViewSet.prepared[0][2],
    )
    body = {"token": token, "password": "Valid-Confirm-Password-927!"}
    first = _dispatch(ExtensionViewSet, "register_confirm", body)
    replay = _dispatch(ExtensionViewSet, "register_confirm", body)

    assert first.status_code == replay.status_code == 201
    assert first.data == replay.data == {
        "code": "Auth.REGISTRATION_CONFIRMED",
        "email": "confirm@example.com",
    }
    assert ExtensionViewSet.applied == [ExtensionViewSet.prepared[0][2]]


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_binding_mismatch_and_failed_application_roll_back_user_and_single_use_code():
    User = get_user_model()
    code = AccessCode.objects.create(code="extension-test-code")
    ExtensionViewSet.policy = _policy(access_code=True, single_use=True)
    request = _dispatch(
        ExtensionViewSet,
        "register_request",
        {
            "email": "rollback@example.com",
            "mode": SIGNUP_MODE_ACCESS_CODE,
            "access_code": code.code,
            "application_value": "opaque-consumer-value",
        },
    )
    assert request.status_code == 201
    code.refresh_from_db()
    assert code.is_active

    token = create_pending_registration_token(
        email="another-registration@example.com",
        mode=SIGNUP_MODE_ACCESS_CODE,
        access_code=code.code,
        registration_binding_ref=ExtensionViewSet.prepared[0][2],
    )
    with pytest.raises(ValueError, match="binding mismatch"):
        _dispatch(
            ExtensionViewSet,
            "register_confirm",
            {"token": token, "password": "Valid-Confirm-Password-927!"},
        )
    code.refresh_from_db()
    assert code.is_active
    assert not User.objects.filter(
        email__iexact="another-registration@example.com"
    ).exists()

    token = create_pending_registration_token(
        email="rollback@example.com",
        mode=SIGNUP_MODE_ACCESS_CODE,
        access_code=code.code,
        registration_binding_ref=ExtensionViewSet.prepared[0][2],
    )
    ExtensionViewSet.fail_application = True
    with pytest.raises(RuntimeError, match="consumer write failed"):
        _dispatch(
            ExtensionViewSet,
            "register_confirm",
            {"token": token, "password": "Valid-Confirm-Password-927!"},
        )

    code.refresh_from_db()
    assert code.is_active
    assert not User.objects.filter(email__iexact="rollback@example.com").exists()
    assert not AccessCode.objects.filter(code="extension-rollback-write").exists()
    assert ExtensionViewSet.applied == []


@pytest.mark.django_db
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_failed_extension_rolls_back_qr_quota_update(monkeypatch):
    from tests.testapp.models import SignupQrTokenStub
    monkeypatch.setattr("django_core_micha.auth.policy.get_signup_qr_token_model", lambda: SignupQrTokenStub)
    qr_token, _ = create_signup_context_token(expires_minutes=60, max_redemptions=1)
    qr_row = SignupQrTokenStub.objects.get()
    _dispatch(
        ExtensionViewSet,
        "register_request",
        {
            "email": "qr-rollback@example.com",
            "mode": SIGNUP_MODE_OPEN,
            "application_value": "hashed-by-consumer",
        },
    )
    binding_ref = ExtensionViewSet.prepared[0][2]
    ExtensionViewSet.fail_application = True
    token = create_pending_registration_token(
        email="qr-rollback@example.com",
        mode=SIGNUP_MODE_OPEN,
        qr_signup_token=qr_token,
        registration_binding_ref=binding_ref,
    )

    with pytest.raises(RuntimeError, match="consumer write failed"):
        _dispatch(
            ExtensionViewSet,
            "register_confirm",
            {"token": token, "password": "Valid-Confirm-Password-927!"},
        )

    qr_row.refresh_from_db()
    assert qr_row.use_count == 0
    assert not AccessCode.objects.filter(code="extension-rollback-write").exists()
    assert not get_user_model().objects.filter(
        email__iexact="qr-rollback@example.com"
    ).exists()


@pytest.mark.django_db
def test_expired_pending_token_is_rejected_before_binding_application():
    payload = signing.loads(
        create_pending_registration_token(
            email="expired@example.com",
            mode=SIGNUP_MODE_OPEN,
            registration_binding_ref="opaque-reference",
        ),
        salt="django_core_micha.auth.pending_registration",
    )
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    expired = signing.dumps(payload, salt="django_core_micha.auth.pending_registration")
    response = _dispatch(
        ExtensionViewSet,
        "register_confirm",
        {"token": expired, "password": "Valid-Confirm-Password-927!"},
    )
    assert response.status_code == 400
    assert response.data == {"code": "Auth.PENDING_TOKEN_EXPIRED"}
    assert ExtensionViewSet.applied == []


@pytest.mark.django_db(transaction=True)
@override_settings(TURNSTILE_SECRET_KEY="", ALLOWED_HOSTS=["app.example.com"])
def test_concurrent_confirmation_does_not_duplicate_consumer_grant():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections, connection
    from tests.testapp.models import RegistrationBindingStub, RegistrationGrantStub

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks are required for the concurrent confirmation test")

    class PersistedExtensionViewSet(ExtensionViewSet):
        def prepare_registration_binding(self, *, email, mode, binding_ref, input_data):
            RegistrationBindingStub.objects.create(reference_hash=hashlib.sha256(binding_ref.encode()).hexdigest(), email=email)

        def apply_registration_binding(self, user, *, binding_ref):
            binding = RegistrationBindingStub.objects.select_for_update().get(reference_hash=hashlib.sha256(binding_ref.encode()).hexdigest(), email=user.email)
            if binding.consumed:
                raise ValueError("binding already consumed")
            RegistrationGrantStub.objects.create(user=user)
            binding.consumed = True
            binding.save(update_fields=["consumed"])

    sent = []
    from unittest.mock import patch
    with patch("django_core_micha.auth.views.send_pending_registration_email", side_effect=lambda **kwargs: sent.append(kwargs["url"])):
        response = _dispatch(PersistedExtensionViewSet, "register_request", {"email": "concurrent@example.com", "mode": SIGNUP_MODE_OPEN, "application_value": "grant"})
    assert response.status_code == 201
    token = sent[0].split("token=", 1)[1]
    barrier = Barrier(2)

    def confirm():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return _dispatch(PersistedExtensionViewSet, "register_confirm", {"token": token, "password": "Valid-Confirm-Password-927!"}).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: confirm(), range(2)))
    assert results == [201, 201]
    assert RegistrationGrantStub.objects.count() == 1
    assert RegistrationBindingStub.objects.get().consumed
    assert get_user_model().objects.filter(email="concurrent@example.com").count() == 1
