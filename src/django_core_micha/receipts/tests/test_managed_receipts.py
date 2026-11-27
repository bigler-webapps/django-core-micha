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


@pytest.fixture
def two_provider_managed(monkeypatch):
    monkeypatch.setattr("django_core_micha.ai_policy.resolver.find_spec", lambda package: object())
    rows = [
        {"driver": "openai_api", "model": "gpt-receipt", "source_id": "openai-source", "efforts": ["high"]},
        {"driver": "anthropic_api", "model": "claude-receipt", "source_id": "anthropic-source", "efforts": ["high"]},
    ]
    with override_settings(
        DCM_AI_POLICY_ENABLED=True,
        DCM_AI_POLICY_APP_ID="bigler-consult",
        DCM_AI_POLICY_ENVIRONMENT="staging",
        DCM_AI_POLICY_SOURCES={
            "openai-source": {"credential_setting": "OPENAI_RECEIPT_KEY"},
            "anthropic-source": {"credential_setting": "ANTHROPIC_RECEIPT_KEY"},
        },
        DCM_RECEIPTS_AI_POLICY_CATALOGUE=rows,
        DCM_RECEIPTS_EFFORT="high",
        OPENAI_RECEIPT_KEY="openai-receipt-secret",
        ANTHROPIC_RECEIPT_KEY="anthropic-receipt-secret",
    ):
        def set_standard(provider):
            source_id = f"{provider}-source"
            model_id = "gpt-receipt" if provider == "openai" else "claude-receipt"
            apply_policy({
                "app_id": "bigler-consult",
                "environment": "staging",
                "revision": 1,
                "allowed": {
                    "openai-source": ["gpt-receipt"],
                    "anthropic-source": ["claude-receipt"],
                },
                "standard": {"source_id": source_id, "model_id": model_id},
            })

        yield set_standard


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


def test_disclosed_second_provider_receives_policy_route(managed, monkeypatch):
    calls = []
    monkeypatch.setattr("django_core_micha.ai_policy.resolver.find_spec", lambda package: object())
    monkeypatch.setattr(reader, "extract_document", lambda request, **kwargs: calls.append(kwargs) or fake_result(payload()))
    rows = [{"driver": "anthropic_api", "model": "claude-receipt", "source_id": "second", "efforts": ["high"]}]
    with override_settings(DCM_RECEIPTS_DISCLOSED_PROVIDERS=["openai", "anthropic"],
            DCM_RECEIPTS_AI_POLICY_CATALOGUE=rows,
            DCM_AI_POLICY_SOURCES={"second": {"credential_setting": "SECOND_KEY"}}, SECOND_KEY="second-secret"):
        apply_policy({"app_id": "bigler-consult", "environment": "staging", "revision": 2,
            "allowed": {"second": ["claude-receipt"]},
            "standard": {"source_id": "second", "model_id": "claude-receipt"}})
        account = user(); grant(account)
        reader.read_receipts(account, image_bytes())
    assert calls == [{"provider": "anthropic", "api_key": "second-secret", "source_id": "second",
        "model": "claude-receipt", "user_id": account.pk}]


@pytest.mark.parametrize("provider,expected", [("openai", "openai_api"), ("anthropic", "anthropic_api")])
def test_unset_disclosure_limits_eligible_drivers(managed, monkeypatch, provider, expected):
    from types import SimpleNamespace
    def resolve(user, *, eligible, **kwargs):
        assert [driver for driver in ("openai_api", "anthropic_api") if eligible(user, {"driver": driver})] == [expected]
        return SimpleNamespace(driver=expected)
    monkeypatch.setattr(reader, "resolve_workload", resolve)
    assert reader._managed_workload(user(), provider, "high").driver == expected


@pytest.mark.parametrize("case", ["undeclared", "unknown", "unavailable"])
def test_disclosure_failures_prevent_dispatch(managed, monkeypatch, case):
    from types import SimpleNamespace
    from unittest.mock import Mock
    extract = Mock()
    monkeypatch.setattr(reader, "extract_document", extract)
    if case == "undeclared":
        monkeypatch.setattr(reader, "resolve_workload", lambda *args, **kwargs: SimpleNamespace(driver="anthropic_api"))
    elif case == "unavailable":
        def unavailable(*args, **kwargs):
            raise ValueError("unavailable")
        monkeypatch.setattr(reader, "resolve_workload", unavailable)
    providers = ["openai", "unknown"] if case == "unknown" else ["openai"]
    with override_settings(DCM_RECEIPTS_DISCLOSED_PROVIDERS=providers):
        account = user(); grant(account)
        with pytest.raises(ReceiptError, match="provider_unavailable"):
            reader.read_receipts(account, image_bytes())
    extract.assert_not_called()


@pytest.mark.parametrize("providers", [[], "openai", None, ["openai", 5]])
def test_invalid_disclosure_prevents_dispatch(managed, monkeypatch, providers):
    from unittest.mock import Mock

    extract = Mock()
    monkeypatch.setattr(reader, "extract_document", extract)
    account = user(); grant(account)
    with override_settings(DCM_RECEIPTS_DISCLOSED_PROVIDERS=providers):
        with pytest.raises(ReceiptError, match="provider_unavailable"):
            reader.read_receipts(account, image_bytes())
    extract.assert_not_called()


def test_two_disclosed_providers_still_require_consent(managed, monkeypatch):
    from unittest.mock import Mock
    extract = Mock()
    monkeypatch.setattr(reader, "extract_document", extract)
    with override_settings(DCM_RECEIPTS_DISCLOSED_PROVIDERS=["openai", "anthropic"]):
        with pytest.raises(ReceiptError, match="consent_required"):
            reader.read_receipts(user(), image_bytes())
    extract.assert_not_called()


@pytest.mark.parametrize(
    "provider,standard_provider,disclosed,expected_dispatch",
    [
        ("openai", "anthropic", None, None),
        ("openai", "anthropic", ["openai", "anthropic"], "anthropic"),
        ("openai", "openai", ["openai", "anthropic"], "openai"),
        ("anthropic", "openai", None, None),
    ],
)
def test_real_resolver_routes_only_to_disclosed_providers(
    two_provider_managed, monkeypatch, provider, standard_provider, disclosed, expected_dispatch
):
    from unittest.mock import Mock

    two_provider_managed(standard_provider)
    extract = Mock(return_value=fake_result(payload()))
    monkeypatch.setattr(reader, "extract_document", extract)
    account = user()
    grant(account)

    with override_settings(DCM_RECEIPTS_PROVIDER=provider):
        if disclosed is None:
            assert not hasattr(reader.settings, "DCM_RECEIPTS_DISCLOSED_PROVIDERS")
            with pytest.raises(ReceiptError, match="provider_unavailable"):
                reader.read_receipts(account, image_bytes())
            extract.assert_not_called()
        else:
            with override_settings(DCM_RECEIPTS_DISCLOSED_PROVIDERS=disclosed):
                reader.read_receipts(account, image_bytes())

    if expected_dispatch:
        extract.assert_called_once()
        assert extract.call_args.kwargs == {
            "provider": expected_dispatch,
            "api_key": f"{expected_dispatch}-receipt-secret",
            "source_id": f"{expected_dispatch}-source",
            "model": "gpt-receipt" if expected_dispatch == "openai" else "claude-receipt",
            "user_id": account.pk,
        }


def test_real_two_provider_route_still_requires_consent(two_provider_managed, monkeypatch):
    from unittest.mock import Mock

    two_provider_managed("anthropic")
    extract = Mock()
    monkeypatch.setattr(reader, "extract_document", extract)

    with override_settings(
        DCM_RECEIPTS_PROVIDER="openai",
        DCM_RECEIPTS_DISCLOSED_PROVIDERS=["openai", "anthropic"],
    ):
        with pytest.raises(ReceiptError, match="consent_required"):
            reader.read_receipts(user(), image_bytes())

    extract.assert_not_called()
