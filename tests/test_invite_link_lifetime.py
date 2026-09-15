from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import RequestFactory, override_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import ScopedRateThrottle

from django_core_micha.emails.email_texts import render_invite_email
from django_core_micha.invitations.mixins import InviteActionsMixin
from django_core_micha.invitations.tokens import InviteTokenGenerator
from django_core_micha.invitations.views import PasswordResetConfirmView


BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="invite-user",
        email="invite@example.com",
        password=None,
    )


def _build_link(user, *, is_new_user, now=BASE_TIME):
    request = RequestFactory().get("/")
    with patch.object(InviteTokenGenerator, "_now", return_value=now), patch.object(
        default_token_generator.__class__, "_now", return_value=now
    ):
        return InviteActionsMixin()._build_frontend_url(
            request, user, is_new_user=is_new_user
        )


def _link_parts(url):
    parts = urlparse(url).path.strip("/").split("/")
    assert len(parts) == 3
    return parts[1], parts[2]


def _confirm_get(uidb64, token):
    request = APIRequestFactory().get(f"/users/password-reset/{uidb64}/{token}/")
    # DRF captures this class attribute at import time, before override_settings
    # can refresh APISettings in this repository's test environment.
    with patch.object(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {"password_reset": "1000/hour"},
    ):
        return PasswordResetConfirmView.as_view()(request, uidb64=uidb64, token=token)


@pytest.mark.django_db
def test_invite_link_is_valid_at_29_days_and_invalid_at_31_days(user):
    invite_url = _build_link(user, is_new_user=True)
    assert urlparse(invite_url).path.startswith("/invite/")
    uidb64, token = _link_parts(invite_url)

    with patch.object(InviteTokenGenerator, "_now", return_value=BASE_TIME + timedelta(days=29)):
        valid_response = _confirm_get(uidb64, token)
    assert valid_response.status_code == 200
    assert valid_response.data["code"] == "Auth.RESET_LINK_VALID"

    with patch.object(InviteTokenGenerator, "_now", return_value=BASE_TIME + timedelta(days=31)):
        invalid_response = _confirm_get(uidb64, token)
    assert invalid_response.status_code == 400
    assert invalid_response.data["code"] == "Auth.RESET_LINK_INVALID"


@pytest.mark.django_db
def test_reset_link_keeps_three_day_lifetime(user):
    reset_url = _build_link(user, is_new_user=False)
    assert urlparse(reset_url).path.startswith("/reset/")
    uidb64, token = _link_parts(reset_url)

    with patch.object(default_token_generator.__class__, "_now", return_value=BASE_TIME + timedelta(days=2)):
        valid_response = _confirm_get(uidb64, token)
    assert valid_response.status_code == 200
    assert valid_response.data["code"] == "Auth.RESET_LINK_VALID"

    with patch.object(default_token_generator.__class__, "_now", return_value=BASE_TIME + timedelta(days=4)):
        invalid_response = _confirm_get(uidb64, token)
    assert invalid_response.status_code == 400
    assert invalid_response.data["code"] == "Auth.RESET_LINK_INVALID"

    # Direct proof of the key-salt separation itself (not just the compound
    # outcome above): a genuine reset token, still well inside what would be
    # the invite generator's own 30-day window, must NOT validate under the
    # invite generator. If the two generators ever shared a key_salt this
    # would return True and the reset link would silently inherit the
    # longer invite lifetime.
    with patch.object(InviteTokenGenerator, "_now", return_value=BASE_TIME + timedelta(days=1)):
        assert InviteTokenGenerator().check_token(user, token) is False


@pytest.mark.django_db
def test_invite_link_is_invalidated_when_user_sets_password(user):
    invite_url = _build_link(user, is_new_user=True)
    uidb64, token = _link_parts(invite_url)

    user.set_password("a-new-invite-password")
    user.save(update_fields=["password"])

    with patch.object(InviteTokenGenerator, "_now", return_value=BASE_TIME + timedelta(days=1)):
        response = _confirm_get(uidb64, token)
    assert response.status_code == 400
    assert response.data["code"] == "Auth.RESET_LINK_INVALID"


@pytest.mark.django_db
@override_settings(INVITE_LINK_TIMEOUT_DAYS=1)
def test_invite_link_uses_configured_lifetime(user):
    invite_url = _build_link(user, is_new_user=True)
    uidb64, token = _link_parts(invite_url)

    # Positive case first: with the overridden 1-day lifetime, a link still
    # within that window is accepted -- proves the setting is actually
    # consulted, not just that the link eventually expires (which an
    # always-invalid generator would also satisfy).
    with patch.object(InviteTokenGenerator, "_now", return_value=BASE_TIME + timedelta(hours=12)):
        valid_response = _confirm_get(uidb64, token)
    assert valid_response.status_code == 200
    assert valid_response.data["code"] == "Auth.RESET_LINK_VALID"

    with patch.object(InviteTokenGenerator, "_now", return_value=BASE_TIME + timedelta(days=2)):
        response = _confirm_get(uidb64, token)
    assert response.status_code == 400
    assert response.data["code"] == "Auth.RESET_LINK_INVALID"


@pytest.mark.django_db
@override_settings(INVITE_LINK_TIMEOUT_DAYS=17)
def test_invite_email_renders_configured_lifetime_in_all_languages(user):
    expected_phrase = {
        "en": "The link is valid for 17 days",
        "de": "Der Link ist 17 Tage gültig",
        "fr": "Le lien est valable 17 jours",
    }
    for language in ("en", "de", "fr"):
        _subject, body = render_invite_email(
            user, "https://example.test/invite/uid/token/", language=language
        )
        assert expected_phrase[language] in body
        assert "30" not in body
