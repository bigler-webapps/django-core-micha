import pytest
from django.test import override_settings

from django_core_micha.privacy.manifest import public_manifest_from_config, validate_manifest_setting

from .conftest import sample_manifest


def test_valid_manifest_with_zero_optional_purposes_has_no_banner_purpose():
    manifest = sample_manifest()
    manifest["purposes"] = [purpose for purpose in manifest["purposes"] if purpose["required"]]
    parsed, errors = validate_manifest_setting(manifest)
    assert errors == []
    assert [purpose["id"] for purpose in parsed["purposes"] if not purpose["required"]] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.update({"internal_key": "secret"}),
        lambda manifest: manifest["controller"].update({"extra": "x"}),
        lambda manifest: manifest["notice"]["documents"]["privacy"].update({"fr": "/privacy/fr/"}),
        lambda manifest: manifest["notice"]["documents"]["privacy"].update({"de": "//evil.test/privacy"}),
        lambda manifest: manifest["notice"]["documents"]["privacy"].update({"de": "https://user:pw@example.test/"}),
        lambda manifest: manifest["notice"]["documents"]["privacy"].update({"de": "javascript:alert(1)"}),
        lambda manifest: manifest["notice"]["documents"]["privacy"].update({"de": "/\\\\evil.test/"}),
    ],
)
def test_manifest_rejects_unknown_keys_incomplete_locales_and_unsafe_references(mutate):
    manifest = sample_manifest()
    mutate(manifest)
    parsed, errors = validate_manifest_setting(manifest)
    assert parsed is None
    assert errors


def test_manifest_rejects_duplicate_purpose_ids():
    manifest = sample_manifest()
    manifest["purposes"][1]["id"] = "analytics"
    parsed, errors = validate_manifest_setting(manifest)
    assert parsed is None
    assert any("purpose IDs" in error for error in errors)


def test_manifest_rejects_duplicate_service_ids_and_shared_storage_ownership():
    manifest = sample_manifest()
    manifest["purposes"][1]["services"][0]["id"] = "first-party-metrics"
    manifest["purposes"][1]["services"][0]["browser_storage"][0]["name"] = "analytics_id"
    parsed, errors = validate_manifest_setting(manifest)
    assert parsed is None
    assert any("service IDs" in error for error in errors)
    assert any("storage name" in error for error in errors)


def test_manifest_rejects_required_purpose_without_justification_and_missing_locale_text():
    manifest = sample_manifest()
    del manifest["purposes"][2]["justification"]
    del manifest["purposes"][0]["label"]["de"]
    parsed, errors = validate_manifest_setting(manifest)
    assert parsed is None
    assert any("justification" in error for error in errors)
    assert any("exactly the declared locales" in error for error in errors)


def test_manifest_rejects_withdrawal_cleanup_for_unowned_storage():
    manifest = sample_manifest()
    manifest["purposes"][0]["withdrawal_clears"] = ["account_session"]
    parsed, errors = validate_manifest_setting(manifest)
    assert parsed is None
    assert any("withdrawal_clears" in error for error in errors)


def test_manifest_rejects_non_string_locales_without_raising():
    manifest = sample_manifest()
    manifest["locales"] = ["en", {"bad": "locale"}]
    parsed, errors = validate_manifest_setting(manifest)
    assert parsed is None
    assert errors


def test_public_manifest_is_a_whitelist_and_has_a_stable_hash(api_client):
    response = api_client.get("/privacy/manifest/")
    payload = response.json()
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert set(payload) == {
        "app", "site", "locales", "controller", "notice", "choice_validity_days",
        "purposes", "necessary_storage", "config_version",
    }
    assert "origins" not in payload
    assert "internal_key" not in payload
    assert payload["necessary_storage"][0]["name"] == "dcm_privacy_sample-site"
    assert payload["purposes"][2]["justification"]
    assert "justification" not in payload["purposes"][0]
    assert public_manifest_from_config(validate_manifest_setting(sample_manifest())[0])["config_version"] == payload["config_version"]


def test_persisted_evidence_schema_contains_no_device_or_account_identifiers():
    from django_core_micha.privacy.models import PrivacyDecision

    field_names = {field.name for field in PrivacyDecision._meta.get_fields()}
    assert not field_names.intersection({"ip", "ip_address", "user_agent", "fingerprint", "user", "referrer", "page"})


def test_invalid_or_absent_manifest_is_not_served(api_client):
    with override_settings(DCM_PRIVACY={"app": "bad"}):
        response = api_client.get("/privacy/manifest/")
    assert response.status_code == 404
    assert response.json() == {"code": "not_configured", "retryable": False}
