from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework.permissions import BasePermission

from django_core_micha.privacy.guard import PrivacyGrantRequired, has_grant, require_grant
from django_core_micha.privacy.models import PrivacyChoice, PrivacyDecision, PrivacyReceipt
from django_core_micha.privacy.permissions import HasPrivacyGrant

from .conftest import post_choice, sample_manifest


pytestmark = pytest.mark.django_db


def request_from(client):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.COOKIES = {key: morsel.value for key, morsel in client.cookies.items()}
    request.user = SimpleNamespace(
        is_authenticated=True,
        accepted_privacy_statement=True,
        accepted_convenience_cookies=True,
    )
    return request


def test_legacy_profile_booleans_never_grant_and_unconfigured_guard_fails_closed(api_client):
    request = request_from(api_client)
    assert has_grant(request, "analytics") is False
    with override_settings(DCM_PRIVACY=None):
        assert has_grant(request, "analytics") is False
        assert has_grant(request, "not-configured") is False


def test_required_purpose_is_a_programmer_error_for_the_guard(api_client):
    with pytest.raises(ImproperlyConfigured):
        has_grant(request_from(api_client), "account-session")


def test_grant_guard_and_permission_factory_require_a_current_optional_grant(api_client):
    request = request_from(api_client)
    permission_class = HasPrivacyGrant("analytics")
    assert isinstance(permission_class, type)
    assert issubclass(permission_class, BasePermission)
    permission = permission_class()
    assert permission.code == "privacy_grant_required"
    assert permission.has_permission(request, None) is False
    with pytest.raises(PrivacyGrantRequired):
        require_grant(request, "analytics")

    response = post_choice(api_client, {"analytics": "granted"})
    assert response.status_code == 200
    assert has_grant(request_from(api_client), "analytics") is True
    assert permission.has_permission(request_from(api_client), None) is True


def test_stale_grant_cannot_overwrite_a_later_withdrawal(api_client):
    refused = post_choice(api_client, {"analytics": "refused"}, expected_seq=0)
    assert refused.status_code == 200
    decisions_before = PrivacyDecision.objects.count()
    stale_grant = post_choice(api_client, {"analytics": "granted"}, expected_seq=0)
    assert stale_grant.status_code == 409
    assert stale_grant.json()["code"] == "stale"
    assert stale_grant.json()["state"]["choices"]["analytics"]["state"] == "refused"
    assert PrivacyDecision.objects.count() == decisions_before
    assert PrivacyReceipt.objects.get().seq == 1
    assert has_grant(request_from(api_client), "analytics") is False


def test_stale_refusal_is_applied_even_when_expected_sequence_is_old(api_client):
    granted = post_choice(api_client, {"analytics": "granted"}, expected_seq=0)
    assert granted.status_code == 200
    refusal = post_choice(api_client, {"analytics": "refused"}, expected_seq=0)
    assert refusal.status_code == 200
    assert refusal.json()["seq"] == 2
    assert refusal.json()["choices"]["analytics"]["state"] == "refused"
    assert list(PrivacyDecision.objects.order_by("seq").values_list("kind", flat=True)) == [
        "granted", "withdrawn",
    ]
    assert has_grant(request_from(api_client), "analytics") is False


def test_replay_is_idempotent_and_does_not_advance_sequence(api_client):
    request_id = "b35b9926-6493-4e74-9cc3-a148d61cc5e2"
    first = post_choice(api_client, {"analytics": "granted"}, expected_seq=0, request_id=request_id)
    replay = post_choice(api_client, {"analytics": "refused"}, expected_seq=0, request_id=request_id)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["seq"] == 1
    assert replay.json()["choices"]["analytics"]["state"] == "granted"
    assert PrivacyReceipt.objects.get().seq == 1
    assert PrivacyDecision.objects.count() == 1


def test_grants_and_refusals_have_equal_server_side_validity(api_client):
    moment = datetime(2026, 11, 6, 12, 0, tzinfo=dt_timezone.utc)
    with patch("django.utils.timezone.now", return_value=moment):
        first = post_choice(api_client, {"analytics": "granted"}, expected_seq=0)
        assert first.status_code == 200
        second = post_choice(api_client, {"personalization": "refused"}, expected_seq=1)
        assert second.status_code == 200
    expected_expiry = moment + timedelta(days=180)
    assert PrivacyChoice.objects.get(purpose_id="analytics").expires_at == expected_expiry
    assert PrivacyChoice.objects.get(purpose_id="personalization").expires_at == expected_expiry


def test_expired_choice_reads_as_unset(api_client):
    moment = datetime(2026, 11, 6, 12, 0, tzinfo=dt_timezone.utc)
    with patch("django.utils.timezone.now", return_value=moment):
        response = post_choice(api_client, {"analytics": "granted"})
    assert response.status_code == 200
    with patch("django.utils.timezone.now", return_value=moment + timedelta(days=181)):
        state = api_client.get("/privacy/choices/").json()
    assert state["receipt_state"] == "present"
    assert state["choices"]["analytics"]["state"] == "unset"
    assert state["choices"]["analytics"]["expires_at"] is None


def test_revision_change_only_resets_that_purpose_and_notice_change_does_not_reset_others(api_client):
    first = post_choice(
        api_client,
        {"analytics": "granted", "personalization": "granted"},
        expected_seq=0,
    )
    assert first.status_code == 200
    revised = sample_manifest()
    revised["purposes"][0]["revision"] = 2
    with override_settings(DCM_PRIVACY=revised):
        state = api_client.get("/privacy/choices/").json()
        assert state["choices"]["analytics"]["state"] == "unset"
        assert state["choices"]["personalization"]["state"] == "granted"

    changed_notice = sample_manifest()
    changed_notice["notice"]["version"] = "privacy-2-informational"
    with override_settings(DCM_PRIVACY=changed_notice):
        state = api_client.get("/privacy/choices/").json()
        assert state["choices"]["analytics"]["state"] == "granted"
        assert state["choices"]["personalization"]["state"] == "granted"
        stale_notice = post_choice(
            api_client,
            {"analytics": "refused"},
            expected_seq=1,
            notice_version="privacy-1",
        )
        assert stale_notice.status_code == 409
        assert stale_notice.json()["code"] == "stale_notice"
        assert stale_notice.json()["state"]["choices"]["analytics"]["state"] == "granted"
        assert PrivacyReceipt.objects.get().seq == 1
