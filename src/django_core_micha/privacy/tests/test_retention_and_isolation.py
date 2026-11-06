from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory
from django.test import override_settings
from django.utils import timezone

from django_core_micha.privacy.guard import receipt_cookie_name
from django_core_micha.privacy.models import PrivacyChoice, PrivacyDecision, PrivacyReceipt
from django_core_micha.privacy.views import choices_view
from django_core_micha.receipts.consent import grant as grant_receipt_consent, state as receipt_consent_state
from django_core_micha.terms.models import TermsAcceptance

from .conftest import choice_body, post_choice


pytestmark = pytest.mark.django_db


def test_pruning_dry_run_then_deletes_only_old_privacy_evidence(api_client):
    now = timezone.now()
    old = PrivacyReceipt.objects.create(
        receipt_id="old-receipt", app="sample-app", site="sample-site",
        created_at=now - timedelta(days=800), seq=1,
    )
    PrivacyChoice.objects.create(
        receipt=old, purpose_id="analytics", purpose_revision=1, state="refused",
        effective_at=now - timedelta(days=500), expires_at=now - timedelta(days=400),
    )
    PrivacyDecision.objects.create(
        receipt=old, purpose_id="analytics", purpose_revision=1, kind="refused", seq=1,
        decided_at=now - timedelta(days=500), locale="en", notice_version="privacy-1",
        channel="settings", request_id="5b536dc3-e1a1-4b78-8fc0-761a2f4bb39e",
    )
    younger = PrivacyReceipt.objects.create(
        receipt_id="younger-receipt", app="sample-app", site="sample-site",
        created_at=now - timedelta(days=30), seq=1,
    )
    PrivacyChoice.objects.create(
        receipt=younger, purpose_id="analytics", purpose_revision=1, state="granted",
        effective_at=now - timedelta(days=30), expires_at=now + timedelta(days=150),
    )
    PrivacyDecision.objects.create(
        receipt=younger, purpose_id="analytics", purpose_revision=1, kind="granted", seq=1,
        decided_at=now - timedelta(days=30), locale="en", notice_version="privacy-1",
        channel="settings", request_id="39d5a7b6-61c8-4fa9-a1ae-64186f94090c",
    )

    output = StringIO()
    call_command("prune_privacy_evidence", dry_run=True, stdout=output)
    assert "Would delete 1 privacy decisions, 1 expired choices, and 1 empty receipts" in output.getvalue()
    assert PrivacyReceipt.objects.count() == 2

    call_command("prune_privacy_evidence", older_than_days=365, verbosity=0)
    assert not PrivacyReceipt.objects.filter(pk=old.pk).exists()
    assert not PrivacyDecision.objects.filter(receipt=old).exists()
    assert not PrivacyChoice.objects.filter(receipt=old).exists()
    assert PrivacyReceipt.objects.filter(pk=younger.pk).exists()
    assert PrivacyDecision.objects.filter(receipt=younger).exists()
    assert PrivacyChoice.objects.filter(receipt=younger).exists()


def test_refusal_leaves_terms_ai_consent_and_authenticated_session_unchanged(api_client):
    import json

    user = get_user_model().objects.create_user(username="privacy-isolation", password="secret")
    terms = TermsAcceptance.objects.create(
        user=user, app="sample-app", version="terms-7", document_url="https://example.test/terms",
    )
    ai_consent = grant_receipt_consent(user)
    grant = post_choice(api_client, {"analytics": "granted"}, expected_seq=0)
    assert grant.status_code == 200
    session_state = {"unrelated-workflow": "keep", "workflow-token": "opaque"}
    request = RequestFactory().post(
        "/privacy/choices/",
        data=json.dumps(choice_body({"analytics": "refused"}, expected_seq=1)),
        content_type="application/json",
        HTTP_HOST="testserver",
        HTTP_ORIGIN="http://testserver",
        REMOTE_ADDR="127.0.0.1",
    )
    request.COOKIES = {
        receipt_cookie_name("sample-site"): grant.cookies[receipt_cookie_name("sample-site")].value,
    }
    request.user = user
    request.session = session_state
    request._dont_enforce_csrf_checks = True
    refusal = choices_view(request)
    assert refusal.status_code == 200

    assert TermsAcceptance.objects.get(pk=terms.pk).accepted_at == terms.accepted_at
    assert receipt_consent_state(user)["granted"] is True
    assert ai_consent.pk is not None
    assert session_state == {"unrelated-workflow": "keep", "workflow-token": "opaque"}
    assert PrivacyReceipt.objects.count() == 1
    assert PrivacyChoice.objects.get().state == "refused"


def test_retention_default_uses_valid_manifest_setting():
    from .conftest import sample_manifest

    manifest = sample_manifest()
    manifest["evidence_retention_days"] = 200
    with override_settings(DCM_PRIVACY=manifest):
        output = StringIO()
        call_command("prune_privacy_evidence", dry_run=True, stdout=output)
    assert "older than 200 days" in output.getvalue()
