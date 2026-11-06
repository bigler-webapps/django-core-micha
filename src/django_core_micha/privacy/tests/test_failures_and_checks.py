from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.checks import run_checks
from django.db import DatabaseError
from django.test import override_settings

from django_core_micha.privacy.models import PrivacyChoice, PrivacyDecision, PrivacyReceipt

from .conftest import post_choice


pytestmark = pytest.mark.django_db


def test_failed_choice_save_rolls_back_and_does_not_set_a_receipt_cookie(api_client):
    with patch(
        "django_core_micha.privacy.services.PrivacyDecision.objects.create",
        side_effect=DatabaseError("simulated write failure"),
    ):
        response = post_choice(api_client, {"analytics": "granted"})
    assert response.status_code == 503
    assert response.json() == {"code": "unavailable", "retryable": True}
    assert not response.cookies
    assert PrivacyReceipt.objects.count() == 0
    assert PrivacyChoice.objects.count() == 0
    assert PrivacyDecision.objects.count() == 0
    assert api_client.get("/privacy/choices/").json()["receipt_state"] == "unset"


def test_manifest_system_check_reports_invalid_configuration():
    with override_settings(DCM_PRIVACY={"app": "invalid"}):
        issues = [issue for issue in run_checks() if issue.id and issue.id.startswith("dcm_privacy.")]
    assert issues
    assert all(issue.id.startswith("dcm_privacy.E") for issue in issues)


def test_manifest_system_check_warns_when_setting_is_absent():
    holder = settings._wrapped
    original = holder.DCM_PRIVACY
    delattr(holder, "DCM_PRIVACY")
    try:
        issues = [issue for issue in run_checks() if issue.id and issue.id.startswith("dcm_privacy.")]
    finally:
        holder.DCM_PRIVACY = original
    assert [issue.id for issue in issues] == ["dcm_privacy.W001"]


def test_choice_and_decision_migration_state_is_current():
    from django.core.management import call_command

    call_command("makemigrations", "dcm_privacy", check=True, dry_run=True, verbosity=0)
