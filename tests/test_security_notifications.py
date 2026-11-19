import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from allauth.account.internal.flows import manage_email, password_change
from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal import flows as totp_flows
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.test import RequestFactory, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import ScopedRateThrottle

from django_core_micha.auth.views import PasskeyViewSet
from django_core_micha.invitations.views import PasswordResetConfirmView
from django_core_micha.invitations.tokens import invite_token_generator


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def notification_settings():
    with override_settings(
    ACCOUNT_ADAPTER="django_core_micha.auth.adapters.CoreAccountAdapter",
    ACCOUNT_EMAIL_NOTIFICATIONS=True,
    ACCOUNT_EMAIL_SUBJECT_PREFIX="[Test] ",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ALLOWED_HOSTS=["testserver"],
    TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}],
    ):
        yield


@pytest.fixture
def user():
    user = get_user_model().objects.create_user(
        username="notice-user", email="old@example.com", password="old-password-123"
    )
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    return user


def make_unusable(user):
    # A plain save() is undone by the prevent_password_wipe pre_save guard.
    get_user_model().objects.filter(pk=user.pk).update(password="!unusable")
    user.refresh_from_db()
    assert not user.has_usable_password()


def request_for(user):
    request = RequestFactory().post("/", REMOTE_ADDR="198.51.100.7")
    request.user = user
    request.session = {}
    return request


def confirm(user, *, invite=False):
    token = (invite_token_generator if invite else default_token_generator).make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    request = APIRequestFactory().post(
        "/reset/", {"new_password": "another-strong-password-731"},
        format="json", REMOTE_ADDR="198.51.100.7",
    )
    with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"password_reset": "1000/hour"}):
        with context.request_context(request):
            return PasswordResetConfirmView.as_view()(request, uidb64=uid, token=token)


def test_shared_default_and_opt_out(user, monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "local")
    from django_core_micha.settings import settings_base
    assert settings_base.ACCOUNT_EMAIL_NOTIFICATIONS is True
    with override_settings(ACCOUNT_EMAIL_NOTIFICATIONS=False):
        response = confirm(user)
    assert response.status_code == 200
    assert not getattr(mail, "outbox", [])


@pytest.mark.parametrize("has_password", [True, False])
def test_reset_and_invite_password_notifications(user, has_password):
    if not has_password:
        make_unusable(user)
    response = confirm(user, invite=not has_password)
    user.refresh_from_db()
    assert response.status_code == 200
    assert user.check_password("another-strong-password-731")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["old@example.com"]
    expected = "Password Reset" if has_password else "Password Set"
    assert expected in mail.outbox[0].subject


@pytest.mark.parametrize("existing", [True, False])
def test_allauth_password_change_and_set(user, existing):
    if not existing:
        make_unusable(user)
    request = request_for(user)
    with patch.object(password_change, "logout_on_password_change", return_value=False), context.request_context(request):
        password_change.change_password(user, "new-password-456")
        finalize = password_change.finalize_password_change if existing else password_change.finalize_password_set
        finalize(request, user)
    assert user.check_password("new-password-456")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["old@example.com"]
    assert ("Password Changed" if existing else "Password Set") in mail.outbox[0].subject


def test_totp_activation_and_deactivation(user):
    request = request_for(user)
    form = SimpleNamespace(secret="JBSWY3DPEHPK3PXP")
    with patch("allauth.mfa.totp.internal.flows.raise_if_reauthentication_required"), context.request_context(request):
        authenticator, _ = totp_flows.activate_totp(request, form)
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]
        assert "Authenticator App Activated" in mail.outbox[0].subject
        mail.outbox.clear()
        totp_flows.deactivate_totp(request, authenticator)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "Authenticator App Deactivated" in mail.outbox[0].subject


def test_changed_email_notifies_previous_address(user):
    request = request_for(user)
    new = EmailAddress.objects.create(user=user, email="new@example.com", verified=True)
    with override_settings(ACCOUNT_REAUTHENTICATION_REQUIRED=False), context.request_context(request):
        assert manage_email.mark_as_primary(request, new)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["old@example.com"]


def test_passkey_removal_sends_notification(user):
    key = Authenticator.objects.create(user=user, type=Authenticator.Type.WEBAUTHN, data={})
    request = APIRequestFactory().delete("/passkeys/1/", REMOTE_ADDR="198.51.100.7")
    force_authenticate(request, user=user)
    with context.request_context(request):
        response = PasskeyViewSet.as_view({"delete": "destroy"})(request, pk=key.pk)
    assert response.status_code == 204
    assert not Authenticator.objects.filter(pk=key.pk).exists()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "Security Key Removed" in mail.outbox[0].subject


def test_delivery_failure_does_not_block_password_change(user, caplog):
    with caplog.at_level(logging.WARNING, logger="django_core_micha.auth.adapters"):
        with patch.object(EmailBackend, "send_messages", side_effect=RuntimeError("old@example.com private mail body")):
            response = confirm(user)
    user.refresh_from_db()
    assert response.status_code == 200
    assert response.data == {"code": "Auth.RESET_PASSWORD_SUCCESS"}
    assert user.check_password("another-strong-password-731")
    assert "account/email/password_reset" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "old@example.com" not in caplog.text
    assert "private mail body" not in caplog.text
