import pytest
from django.test import override_settings

from django_core_micha.ai_policy.policy import apply_policy
from django_core_micha.receipts import reader
from django_core_micha.receipts.consent import grant
from django_core_micha.receipts.errors import ReceiptError

from .test_receipts import fake_result, image_bytes, payload, user

pytestmark=pytest.mark.django_db


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr("django_core_micha.ai_policy.resolver.find_spec",
        lambda package: object() if package=="openai" else None)
    rows = [
        {"driver": "openai_api", "model": "managed-receipt-model", "source_id": "central", "efforts": ["high"]},
        {"driver": "openai_api", "model": "other-receipt-model", "source_id": "other", "efforts": ["high"]},
    ]
    with override_settings(
        DCM_AI_POLICY_ENABLED=True,
        DCM_AI_POLICY_APP_ID="bigler-consult",
        DCM_AI_POLICY_ENVIRONMENT="staging",
        DCM_AI_POLICY_SOURCES={
            "central": {"credential_setting": "CENTRAL_RECEIPT_KEY"},
            "other": {"credential_setting": "OTHER_RECEIPT_KEY"},
        },
        DCM_RECEIPTS_AI_POLICY_CATALOGUE=rows,
        DCM_RECEIPTS_PROVIDER="openai",
        DCM_RECEIPTS_MODEL="legacy-receipt-model",
        DCM_RECEIPTS_EFFORT="high",
        CENTRAL_RECEIPT_KEY="central-receipt-secret",
        OTHER_RECEIPT_KEY="other-receipt-secret",
        OPENAI_API_KEY="legacy-secret",
    ):
        apply_policy({"app_id":"bigler-consult","environment":"staging","revision":1,
            "allowed":{"central":["managed-receipt-model"]},
            "standard":{"source_id":"central","model_id":"managed-receipt-model"}})
        yield


def test_managed_reader_dispatches_standard_with_source_credential(managed,monkeypatch):
    calls=[]

    def fake_extract(request,**kwargs):
        calls.append((request,kwargs))
        return fake_result(payload())

    monkeypatch.setattr(reader,"extract_document",fake_extract)
    account=user();grant(account)

    assert reader.read_receipts(account,image_bytes())[0]["merchant"]=="Cafe"
    request,kwargs=calls[0]
    assert kwargs=={"provider":"openai","api_key":"central-receipt-secret","source_id":"central",
        "model":"managed-receipt-model","user_id":account.pk}
    assert request.effort == "high"

    apply_policy({
        "app_id": "bigler-consult", "environment": "staging", "revision": 2,
        "allowed": {"central": ["managed-receipt-model"], "other": ["other-receipt-model"]},
        "standard": {"source_id": "other", "model_id": "other-receipt-model"},
    })
    reader.read_receipts(account, image_bytes())
    assert calls[-1][1] == {
        "provider": "openai", "api_key": "other-receipt-secret", "source_id": "other",
        "model": "other-receipt-model", "user_id": account.pk,
    }


def test_managed_reader_refuses_incompatible_effort(managed, monkeypatch):
    from unittest.mock import Mock

    extract = Mock()
    monkeypatch.setattr(reader, "extract_document", extract)
    account = user()
    grant(account)

    with override_settings(DCM_RECEIPTS_EFFORT="low"):
        with pytest.raises(ReceiptError, match="provider_unavailable"):
            reader.read_receipts(account, image_bytes())

    extract.assert_not_called()


def test_ineligible_standard_refuses_before_provider_dispatch(managed,monkeypatch):
    calls=[]
    monkeypatch.setattr(reader,"extract_document",lambda *args,**kwargs:calls.append(kwargs))
    with override_settings(DCM_AI_POLICY_SOURCES={
        "central":{"credential_setting":"CENTRAL_RECEIPT_KEY","eligible":False},
    }):
        account=user();grant(account)
        with pytest.raises(ReceiptError) as exc:
            reader.read_receipts(account,image_bytes())
    assert exc.value.code=="provider_unavailable" and calls==[]


def test_consent_refusal_precedes_managed_provider_dispatch(managed,monkeypatch):
    calls=[]
    monkeypatch.setattr(reader,"extract_document",lambda *args,**kwargs:calls.append(kwargs))
    with pytest.raises(ReceiptError) as exc:
        reader.read_receipts(user(),image_bytes())
    assert exc.value.code=="consent_required" and calls==[]


def test_unenrolled_reader_keeps_legacy_provider_and_model(monkeypatch):
    calls=[]

    def fake_extract(request,**kwargs):
        calls.append((request,kwargs))
        return fake_result(payload())

    monkeypatch.setattr(reader,"extract_document",fake_extract)
    with override_settings(
        DCM_AI_POLICY_ENABLED=False,
        DCM_RECEIPTS_PROVIDER="anthropic",
        DCM_RECEIPTS_MODEL="legacy-receipt-model",
        DCM_RECEIPTS_EFFORT="high",
        ANTHROPIC_API_KEY="legacy-anthropic-secret",
    ):
        account=user();grant(account)
        reader.read_receipts(account,image_bytes())
    request,kwargs=calls[0]
    assert kwargs=={"provider":"anthropic","api_key":"legacy-anthropic-secret",
        "model":"legacy-receipt-model","user_id":account.pk}
    assert request.effort=="high"
