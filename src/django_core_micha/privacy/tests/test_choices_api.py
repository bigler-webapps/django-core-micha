import json
import time
from unittest.mock import patch

import pytest
from django.core import signing
from django.test import Client, override_settings

from django_core_micha.privacy.guard import RECEIPT_SALT, receipt_cookie_name
from django_core_micha.privacy.models import PrivacyChoice, PrivacyDecision, PrivacyReceipt

from .conftest import choice_body, post_choice, sample_manifest


pytestmark = pytest.mark.django_db


def test_choices_default_to_unset_without_creating_a_receipt_or_cookie(api_client):
    response = api_client.get("/privacy/choices/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json()["receipt_state"] == "unset"
    assert response.json()["seq"] == 0
    assert response.json()["choices"]["analytics"] == {
        "state": "unset", "revision": 1, "expires_at": None,
    }
    assert receipt_cookie_name("sample-site") not in response.cookies
    assert PrivacyReceipt.objects.count() == 0


def test_first_successful_write_sets_signed_host_only_receipt_cookie(api_client):
    response = post_choice(api_client, {"analytics": "granted"})
    cookie_name = receipt_cookie_name("sample-site")
    cookie = response.cookies[cookie_name]
    assert response.status_code == 200
    assert cookie.value
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/"
    assert cookie["domain"] == ""
    assert cookie["max-age"] == 180 * 86400
    state = api_client.get("/privacy/choices/").json()
    assert state["receipt_state"] == "present"
    assert state["choices"]["analytics"]["state"] == "granted"


def test_cookie_is_browser_scoped_and_not_account_synchronized(api_client):
    response = post_choice(api_client, {"analytics": "granted"})
    cookie = response.cookies[receipt_cookie_name("sample-site")].value
    other_browser = Client()
    assert other_browser.get("/privacy/choices/").json()["receipt_state"] == "unset"
    other_browser.cookies[receipt_cookie_name("sample-site")] = cookie
    assert other_browser.get("/privacy/choices/").json()["choices"]["analytics"]["state"] == "granted"


def test_forged_foreign_and_unknown_receipts_read_as_unset(api_client):
    name = receipt_cookie_name("sample-site")
    with patch("django.core.signing.time.time", return_value=time.time() - 181 * 86400):
        expired = signing.dumps(
            {"app": "sample-app", "site": "sample-site", "receipt_id": "old"},
            salt=RECEIPT_SALT,
        )
    values = [
        "not.a.valid.signature",
        signing.dumps(
            {"app": "other-app", "site": "sample-site", "receipt_id": "foreign"},
            salt=RECEIPT_SALT,
        ),
        signing.dumps(
            {"app": "sample-app", "site": "other-site", "receipt_id": "foreign"},
            salt=RECEIPT_SALT,
        ),
        signing.dumps(
            {"app": "sample-app", "site": "sample-site", "receipt_id": "missing"},
            salt=RECEIPT_SALT,
        ),
        expired,
    ]
    for value in values:
        api_client.cookies[name] = value
        state = api_client.get("/privacy/choices/").json()
        assert state["receipt_state"] == "unset"
        assert state["choices"]["analytics"]["state"] == "unset"
    assert PrivacyReceipt.objects.count() == 0


def test_bad_origin_and_csrf_fail_with_json_403(api_client):
    body = choice_body({"analytics": "granted"})
    bad_origin = api_client.post(
        "/privacy/choices/", data=json.dumps(body), content_type="application/json",
        HTTP_HOST="testserver", HTTP_ORIGIN="https://evil.test",
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["code"] == "origin_not_allowed"
    assert bad_origin["Cache-Control"] == "no-store"

    strict_client = Client(enforce_csrf_checks=True)
    csrf_failure = strict_client.post(
        "/privacy/choices/", data=json.dumps(body), content_type="application/json",
        HTTP_HOST="testserver", HTTP_ORIGIN="http://testserver",
    )
    assert csrf_failure.status_code == 403
    assert csrf_failure["Content-Type"].startswith("application/json")
    assert csrf_failure.json()["code"] == "csrf_failed"
    assert PrivacyReceipt.objects.count() == 0


def test_configured_origin_is_allowed_by_the_shared_boundary(api_client):
    manifest = sample_manifest()
    manifest["origins"] = ["https://allowed.test"]
    with override_settings(DCM_PRIVACY=manifest):
        response = api_client.post(
            "/privacy/choices/",
            data=json.dumps(choice_body({"analytics": "refused"})),
            content_type="application/json",
            HTTP_HOST="testserver",
            HTTP_ORIGIN="https://allowed.test",
        )
    assert response.status_code == 200


def test_oversize_invalid_and_unconfigured_purposes_write_nothing(api_client):
    large = api_client.post(
        "/privacy/choices/", data=" " * 4097, content_type="application/json",
        HTTP_HOST="testserver", HTTP_ORIGIN="http://testserver",
    )
    assert large.status_code == 413
    assert large.json()["code"] == "too_large"

    unknown = post_choice(api_client, {"not-configured": "granted"})
    required = post_choice(api_client, {"account-session": "granted"})
    assert unknown.status_code == required.status_code == 422
    assert unknown.json()["code"] == required.json()["code"] == "unknown_purpose"
    assert PrivacyReceipt.objects.count() == 0
    assert PrivacyChoice.objects.count() == 0
    assert PrivacyDecision.objects.count() == 0


def test_write_throttle_is_retryable_and_has_retry_after(api_client, monkeypatch):
    monkeypatch.setattr("django_core_micha.privacy.views._throttle", lambda *args: False)
    response = post_choice(api_client, {"analytics": "refused"})
    assert response.status_code == 429
    assert response.json() == {"code": "throttled", "retryable": True}
    assert response["Retry-After"] == "3600"
    assert PrivacyReceipt.objects.count() == 0


def test_receipt_creation_has_a_separate_smaller_throttle(api_client, monkeypatch):
    monkeypatch.setattr(
        "django_core_micha.privacy.views._throttle",
        lambda bucket, identity, limit: limit != 10,
    )
    response = post_choice(api_client, {"analytics": "refused"})
    assert response.status_code == 429
    assert response.json()["retryable"] is True
    assert response["Retry-After"] == "3600"
    assert PrivacyReceipt.objects.count() == 0


def test_unknown_request_fields_are_rejected_without_account_ids(api_client):
    body = choice_body({"analytics": "granted"}, user_id=999)
    response = api_client.post(
        "/privacy/choices/", data=json.dumps(body), content_type="application/json",
        HTTP_HOST="testserver", HTTP_ORIGIN="http://testserver",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert PrivacyReceipt.objects.count() == 0
