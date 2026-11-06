from datetime import timedelta
from io import StringIO

import pytest
from django.core import signing
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from django_core_micha.privacy.guard import RECEIPT_SALT, has_grant, receipt_cookie_name
from django_core_micha.privacy.models import PrivacyChoice, PrivacyDecision, PrivacyReceipt

from .conftest import post_choice, sample_manifest
from .test_guard_and_ordering import request_from


pytestmark = pytest.mark.django_db


def test_write_with_an_old_config_version_is_rejected_and_stores_nothing(api_client):
    stale_version = None
    from .conftest import current_config_version

    stale_version = current_config_version()
    revised = sample_manifest()
    revised["purposes"][0]["revision"] = 2
    with override_settings(DCM_PRIVACY=revised):
        response = post_choice(api_client, {"analytics": "granted"}, config_version=stale_version)
        assert response.status_code == 409
        assert response.json()["code"] == "stale_config"
        assert response.json()["state"]["choices"]["analytics"]["revision"] == 2
    assert PrivacyReceipt.objects.count() == 0
    assert PrivacyDecision.objects.count() == 0
    assert PrivacyChoice.objects.count() == 0
    assert receipt_cookie_name("sample-site") not in response.cookies


def test_guard_checks_service_declared_under_the_purpose(api_client):
    assert post_choice(api_client, {"analytics": "granted"}).status_code == 200
    request = request_from(api_client)
    assert has_grant(request, "analytics") is True
    assert has_grant(request, "analytics", "first-party-metrics") is True
    assert has_grant(request, "analytics", "undeclared-sdk") is False
    # A service of another purpose never inherits this purpose's grant.
    assert has_grant(request, "analytics", "theme-preference") is False


def test_guard_denies_after_expiry_and_after_revision_change(api_client):
    assert post_choice(api_client, {"analytics": "granted"}).status_code == 200
    request = request_from(api_client)
    assert has_grant(request, "analytics") is True
    PrivacyChoice.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert has_grant(request, "analytics") is False
    PrivacyChoice.objects.update(expires_at=timezone.now() + timedelta(days=1))
    assert has_grant(request, "analytics") is True
    revised = sample_manifest()
    revised["purposes"][0]["revision"] = 2
    with override_settings(DCM_PRIVACY=revised):
        assert has_grant(request, "analytics") is False


@pytest.mark.parametrize("row_app,row_site", [("other-app", "sample-site"), ("sample-app", "other-site")])
def test_signed_cookie_for_a_stored_receipt_of_another_scope_reads_as_unset(api_client, row_app, row_site):
    now = timezone.now()
    foreign = PrivacyReceipt.objects.create(receipt_id="foreign-id", app=row_app, site=row_site, seq=1)
    PrivacyChoice.objects.create(
        receipt=foreign, purpose_id="analytics", purpose_revision=1, state="granted",
        effective_at=now, expires_at=now + timedelta(days=180),
    )
    cookie = signing.dumps(
        {"app": "sample-app", "site": "sample-site", "receipt_id": "foreign-id"}, salt=RECEIPT_SALT
    )
    api_client.cookies[receipt_cookie_name("sample-site")] = cookie
    state = api_client.get("/privacy/choices/").json()
    assert state["receipt_state"] == "unset"
    assert state["choices"]["analytics"]["state"] == "unset"
    assert has_grant(request_from(api_client), "analytics") is False


def test_no_receipt_choice_or_decision_field_carries_account_or_device_identifiers():
    forbidden = {"ip", "ip_address", "user_agent", "fingerprint", "user", "user_id", "referrer", "page"}
    for model in (PrivacyReceipt, PrivacyChoice, PrivacyDecision):
        names = {field.name for field in model._meta.get_fields()}
        assert not names.intersection(forbidden), model.__name__


def test_choices_are_pruned_from_the_decision_date_not_from_expiry():
    now = timezone.now()
    receipt = PrivacyReceipt.objects.create(
        receipt_id="decided-long-ago", app="sample-app", site="sample-site", seq=1,
        created_at=now - timedelta(days=400),
    )
    PrivacyChoice.objects.create(
        receipt=receipt, purpose_id="analytics", purpose_revision=1, state="granted",
        effective_at=now - timedelta(days=400), expires_at=now - timedelta(days=220),
    )
    call_command("prune_privacy_evidence", verbosity=0)
    assert not PrivacyChoice.objects.exists()
    assert not PrivacyReceipt.objects.exists()


def test_configured_retention_window_is_the_cutoff_that_is_actually_applied():
    manifest = sample_manifest()
    manifest["evidence_retention_days"] = 200
    now = timezone.now()
    receipt = PrivacyReceipt.objects.create(
        receipt_id="window-receipt", app="sample-app", site="sample-site", seq=2,
        created_at=now - timedelta(days=260),
    )
    for age, request_id in ((250, "0b1e8d6a-5c0e-4a5e-9d6e-1e8a8b8a0001"), (150, "0b1e8d6a-5c0e-4a5e-9d6e-1e8a8b8a0002")):
        PrivacyDecision.objects.create(
            receipt=receipt, purpose_id="analytics", purpose_revision=1, kind="granted", seq=1,
            decided_at=now - timedelta(days=age), locale="en", notice_version="privacy-1",
            channel="settings", request_id=request_id,
        )
    PrivacyChoice.objects.create(
        receipt=receipt, purpose_id="analytics", purpose_revision=1, state="granted",
        effective_at=now - timedelta(days=150), expires_at=now + timedelta(days=30),
    )
    with override_settings(DCM_PRIVACY=manifest):
        call_command("prune_privacy_evidence", stdout=StringIO())
    remaining = list(PrivacyDecision.objects.values_list("request_id", flat=True))
    assert remaining == ["0b1e8d6a-5c0e-4a5e-9d6e-1e8a8b8a0002"]
    assert PrivacyChoice.objects.count() == 1
    assert PrivacyReceipt.objects.filter(pk=receipt.pk).exists()
