import copy
import json
from types import SimpleNamespace as NS

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.ai_policy.models import AIModelPolicy
from django_core_micha.ai_policy.policy import PolicyError, apply_policy, validate_policy
from django_core_micha.ai_policy.resolver import resolve_workload
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
from django_core_micha.billing.models import AICostRecord

pytestmark = pytest.mark.django_db


FAMILIES = (
    ("openai", "astra", "openai-main", "astra-v1", ["chat", "completion"]),
    ("openai", "sol", "openai-main", "sol-v1", ["chat", "completion"]),
    ("openai", "terra", "openai-main", "terra-v1", ["chat", "completion"]),
    ("openai", "luna", "openai-main", "luna-v1", ["chat", "completion", "extraction"]),
    ("claude", "fable", "claude-api", "fable-v1", ["chat", "completion"]),
    ("claude", "opus", "claude-api", "opus-v1", ["chat", "completion"]),
    ("claude", "sonnet", "claude-api", "sonnet-v1", ["chat", "completion"]),
    ("claude", "haiku", "claude-sub", "haiku-v1", ["chat", "completion"]),
)

ALLOWED = {
    "openai-main": ["astra-v1", "sol-v1", "terra-v1", "luna-v1"],
    "openai-other": ["astra-v2"],
    "claude-api": ["fable-v1", "opus-v1", "sonnet-v1"],
    "claude-sub": ["haiku-v1", "sonnet-sub-v1"],
}

SOURCE_SETTINGS = {
    "openai-main": {"driver": "openai_api", "label": "Private OpenAI account", "credential_setting": "OPENAI_MAIN_KEY"},
    "openai-other": {"driver": "openai_api", "label": "Second OpenAI account", "credential_setting": "OPENAI_OTHER_KEY"},
    "claude-api": {"driver": "anthropic_api", "label": "Private Anthropic account", "credential_setting": "CLAUDE_API_KEY"},
    "claude-sub": {"driver": "claude_cli", "label": "Claude subscription", "credential_setting": "CLAUDE_SUB_KEY", "billing_source": "subscription"},
}


def policy_payload(*, revision=1, astra_source="openai-main", astra_model="astra-v1",
                   sonnet_source="claude-api", sonnet_model="sonnet-v1", omit=(), execution=False):
    families = []
    for provider, family, source, model, capabilities in FAMILIES:
        if family in omit:
            continue
        if family == "astra":
            source, model = astra_source, astra_model
        if family == "sonnet":
            source, model = sonnet_source, sonnet_model
        choice = {"provider": provider, "family": family, "source_id": source, "model_id": model,
                  "capabilities": list(capabilities), "efforts": ["low", "medium"]}
        if family == "sonnet":
            choice["legacy"] = [{"source_id": "legacy-claude", "model_id": "claude-old-sonnet"}]
        if family == "astra" and execution:
            choice["capabilities"].append("code_execution")
            choice["code_execution"] = {"enabled": True, "tool_version": "code_interpreter",
                "memory_limit": "1g", "max_execution_seconds": 90, "max_continuations": 3,
                "usd_micros": 30_000, "unit": "openai_container_20m"}
        families.append(choice)
    result = {
        "app_id": "family-test-app", "environment": "staging", "revision": revision,
        "allowed": copy.deepcopy(ALLOWED),
        "standard": {"source_id": "openai-main", "model_id": "astra-v1"},
        "families": families,
        "default_family": {"provider": "openai", "family": "astra"},
    }
    if execution:
        result["verified_execution"] = [{"source_id": astra_source, "model_id": astra_model,
            "driver": "openai_api", "tool_version": "code_interpreter", "evidence": "synthetic-provider-evidence"}]
    return result


def catalogue_rows():
    return [{"provider": provider, "family": family, "efforts": ["low", "medium"],
             "capabilities": ["chat", "completion", "extraction", "code_execution"]}
            for provider, family, *_ in FAMILIES]


@pytest.fixture
def managed_families(monkeypatch):
    from django_core_micha.ai_policy import resolver
    monkeypatch.setattr(resolver, "find_spec", lambda package: object() if package in {"openai", "anthropic"} else None)
    monkeypatch.setattr(resolver, "which", lambda name: "/synthetic/claude" if name == "claude" else None)
    configured = override_settings(
        DCM_AI_POLICY_ENABLED=True,
        DCM_AI_POLICY_APP_ID="family-test-app",
        DCM_AI_POLICY_ENVIRONMENT="staging",
        DCM_AI_POLICY_SOURCES=copy.deepcopy(SOURCE_SETTINGS),
        OPENAI_MAIN_KEY="synthetic-openai-main",
        OPENAI_OTHER_KEY="synthetic-openai-other",
        CLAUDE_API_KEY="synthetic-claude-api",
        CLAUDE_SUB_KEY="synthetic-claude-subscription",
        OPENAI_API_KEY="legacy-openai-key",
        ANTHROPIC_API_KEY="legacy-anthropic-key",
        CLAUDE_CODE_OAUTH_TOKEN="legacy-claude-token",
        DCM_ASSISTANT_CATALOGUE=catalogue_rows(),
        DCM_ASSISTANT_SCOPE_PERMISSION=lambda _user, _scope: True,
        DCM_ASSISTANT_TURN_VALIDITY=lambda *_args: True,
        DCM_ASSISTANT_SHARED_SCOPE=lambda _scope: False,
        DCM_ASSISTANT_USAGE_HOOK=lambda *_args: None,
        DCM_AI_USAGE_SETTLEMENT_HOOK=lambda *_args: None,
    )
    configured.enable()
    apply_policy(policy_payload())
    try:
        yield
    finally:
        configured.disable()


def _user(name="family-user"):
    return get_user_model().objects.create_user(username=name, password="synthetic-test-password")


def _catalogue(user):
    from django_core_micha.assistant.views import CatalogueView
    request = APIRequestFactory().get("/assistant/models/")
    force_authenticate(request, user=user)
    return CatalogueView.as_view()(request).data


def test_managed_catalogue_lists_only_stable_families_and_hides_all_route_metadata(managed_families):
    user = _user()
    response = _catalogue(user)
    assert {(row["provider"], row["family"], row["label"]) for row in response["entries"]} == {
        ("openai", "astra", "Astra"), ("openai", "sol", "Sol"),
        ("openai", "terra", "Terra"), ("openai", "luna", "Luna"),
        ("claude", "fable", "Fable"), ("claude", "opus", "Opus"),
        ("claude", "sonnet", "Sonnet"), ("claude", "haiku", "Haiku"),
    }
    assert len([row for row in response["entries"] if row["provider"] == "openai" and row["family"] == "astra"]) == 1
    assert response["default"]["provider"] == "openai" and response["default"]["family"] == "astra"
    rendered = json.dumps(response)
    for forbidden in ("source_id", "source_label", "model_id", "driver", "billing", "credential",
                      "openai-main", "claude-api", "claude-sub", "Private Anthropic account",
                      "Claude subscription", "synthetic-claude-api"):
        assert forbidden not in rendered

    from django_core_micha.assistant.views import AvailabilityView
    with override_settings(DCM_ASSISTANT_AVAILABILITY=lambda *_args: "synthetic_scope_restriction"):
        request = APIRequestFactory().get("/assistant/status/?scope=private")
        force_authenticate(request, user=user)
        unavailable = AvailabilityView.as_view()(request).data
    assert unavailable["reason"] == "synthetic_scope_restriction"
    assert "providers" not in unavailable and "source_id" not in json.dumps(unavailable)

    state = AIModelPolicy.objects.get(pk=1)
    assert set(state.allowed) == {"openai-main", "openai-other", "claude-api", "claude-sub"}
    assert SOURCE_SETTINGS["claude-api"]["driver"] == "anthropic_api"
    assert SOURCE_SETTINGS["claude-sub"]["driver"] == "claude_cli"


def test_policy_rejects_unknown_cross_provider_and_disallowed_defaults(managed_families):
    valid = policy_payload()
    assert validate_policy(valid).default_family == "astra"

    unknown = copy.deepcopy(valid)
    unknown["families"][0]["family"] = "nova"
    with pytest.raises(PolicyError, match="invalid_policy"):
        validate_policy(unknown)

    cross_provider = copy.deepcopy(valid)
    cross_provider["families"][0]["family"] = "sonnet"
    with pytest.raises(PolicyError, match="invalid_policy"):
        validate_policy(cross_provider)

    disallowed_default = copy.deepcopy(valid)
    disallowed_default["default_family"] = {"provider": "claude", "family": "sonnet"}
    disallowed_default["families"] = [row for row in disallowed_default["families"] if row["family"] != "sonnet"]
    with pytest.raises(PolicyError, match="invalid_policy"):
        validate_policy(disallowed_default)

    with override_settings(DCM_AI_POLICY_SOURCES={**SOURCE_SETTINGS,
            "openai-main": {**SOURCE_SETTINGS["openai-main"], "eligible": False}}):
        with pytest.raises(PolicyError, match="invalid_policy"):
            validate_policy(valid)


def test_execution_evidence_is_private_source_and_model_specific(managed_families):
    payload = policy_payload(revision=2, execution=True)
    wrong = copy.deepcopy(payload)
    wrong["verified_execution"][0]["driver"] = "anthropic_api"
    with pytest.raises(PolicyError, match="invalid_policy"):
        validate_policy(wrong)
    wrong = copy.deepcopy(payload)
    wrong["verified_execution"][0]["model_id"] = "unknown-model"
    with pytest.raises(PolicyError, match="invalid_policy"):
        validate_policy(wrong)
    payload["verified_execution"] = []
    apply_policy(payload)
    from django_core_micha.assistant.execution import model_supports_execution
    row = resolve_workload(_user("no-execution-evidence"), rows=catalogue_rows(), provider="openai",
                           family="astra", workload="chat", refresh=False).row
    with override_settings(DCM_AI_EXECUTION_CAPABILITY_CHECK=lambda *_args: True):
        assert not model_supports_execution(row)


def test_family_selection_and_revocation_through_chat_api(managed_families):
    from django_core_micha.assistant.views import ChatDetailView, ChatListCreateView

    user = _user("family-api")
    factory = APIRequestFactory()
    request = factory.post("/", {"selection": {"entry": "claude:sonnet", "effort": "low"}}, format="json")
    force_authenticate(request, user=user)
    created = ChatListCreateView.as_view()(request)
    assert created.status_code == 201
    assert created.data["selection"]["family"] == "sonnet"
    request = factory.patch("/", {"selection": {"entry": "openai:sol", "effort": "medium"}}, format="json")
    force_authenticate(request, user=user)
    updated = ChatDetailView.as_view()(request, chat_id=created.data["id"])
    assert updated.data["selection"] == {"entry": "openai:sol", "provider": "openai", "family": "sol", "effort": "medium"}
    request = factory.patch("/", {"effort": None}, format="json")
    force_authenticate(request, user=user)
    assert ChatDetailView.as_view()(request, chat_id=created.data["id"]).status_code == 400
    revoked_chat = AssistantChat.objects.create(owner=user, selected_provider="claude", selected_family="sonnet",
        selected_driver="anthropic_api", selected_source_id="claude-api", selected_model="sonnet-v1", selected_effort="low")
    apply_policy(policy_payload(revision=2, omit=("sonnet",)))
    assert not any(row["family"] == "sonnet" for row in _catalogue(user)["entries"])
    from django_core_micha.assistant.turns import _start
    turn = _start(revoked_chat.pk, user.pk, "after revocation")
    assert (turn.provider, turn.family) == ("openai", "astra")


def test_legacy_local_catalogue_does_not_permit_unlisted_family_route(managed_families):
    user = _user("family-local-restriction")
    legacy_rows = [{"source_id": "openai-main", "driver": "openai_api", "model": "astra-v1"}]
    assert resolve_workload(user, rows=legacy_rows, provider="openai", family="astra",
                            workload="chat", refresh=False).source_id == "openai-main"
    with pytest.raises(ValueError, match="invalid_selection"):
        resolve_workload(user, rows=legacy_rows, provider="claude", family="sonnet",
                         workload="chat", refresh=False)


def test_source_and_version_switch_keep_saved_family_and_stale_revision_is_ignored(managed_families):
    user = _user("family-route-switch")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra",
        selected_driver="openai_api", selected_model="astra-v1", selected_source_id="openai-main", selected_effort="low")

    apply_policy(policy_payload(revision=2, astra_source="openai-other", astra_model="astra-v2",
                                sonnet_source="claude-sub", sonnet_model="sonnet-sub-v1"))
    turn = __import__("django_core_micha.assistant.turns", fromlist=["_start"])._start(chat.pk, user.pk, "route after policy update")
    chat.refresh_from_db()
    assert (turn.provider, turn.family, turn.source_id, turn.model) == ("openai", "astra", "openai-other", "astra-v2")
    assert (chat.selected_provider, chat.selected_family, chat.selected_source_id, chat.selected_model) == (
        "openai", "astra", "openai-other", "astra-v2")
    assert resolve_workload(user, rows=catalogue_rows(), provider="claude", family="sonnet",
        workload="chat", refresh=False).source_id == "claude-sub"
    assert apply_policy(policy_payload(revision=1)) is False
    assert AIModelPolicy.objects.get(pk=1).revision == 2

    public = _catalogue(user)
    sonnet = [row for row in public["entries"] if row["provider"] == "claude" and row["family"] == "sonnet"]
    assert len(sonnet) == 1 and sonnet[0]["id"] == "claude:sonnet"
    assert "claude-sub" not in json.dumps(public) and "subscription" not in json.dumps(public).lower()


def test_legacy_selection_reconciles_only_when_authoritatively_mapped(managed_families):
    from django_core_micha.assistant.drivers.base import DriverError
    from django_core_micha.assistant.turns import _start, turn_payload

    user = _user("legacy-family-selection")
    recognized = AssistantChat.objects.create(owner=user, selected_driver="anthropic_api",
        selected_model="claude-old-sonnet", selected_source_id="legacy-claude", selected_effort="low")
    turn = _start(recognized.pk, user.pk, "recognized legacy family")
    assert (turn.provider, turn.family, turn.source_id, turn.model) == (
        "claude", "sonnet", "claude-api", "sonnet-v1")
    recognized.refresh_from_db()
    assert recognized.selected_provider == "claude" and recognized.selected_family == "sonnet"

    without_source = AssistantChat.objects.create(owner=user, selected_driver="anthropic_api",
        selected_model="claude-old-sonnet", selected_effort="low")
    mapped = _start(without_source.pk, user.pk, "legacy without source")
    assert (mapped.provider, mapped.family) == ("claude", "sonnet")

    old = AssistantTurn.objects.create(chat=recognized, sender=user, sequence=2, user_text="old", state="completed",
        driver="anthropic_api", model="claude-old-sonnet", source_id="legacy-claude")
    payload = turn_payload(old)
    assert payload["provider"] == "claude" and payload["family"] == "sonnet"
    assert not {"source_id", "model", "driver", "billing_source"} & payload.keys()

    unresolved = AssistantChat.objects.create(owner=user, selected_driver="openai_api",
        selected_model="unlisted-old-model", selected_source_id="removed-account", selected_effort="low")
    with pytest.raises(DriverError, match="unavailable"):
        _start(unresolved.pk, user.pk, "unresolved legacy choice")
    unresolved.selected_source_id = ""
    unresolved.save(update_fields=("selected_source_id",))
    with pytest.raises(DriverError, match="unavailable"):
        _start(unresolved.pk, user.pk, "unresolved without source")


def test_running_turn_keeps_its_resolved_route_while_next_turn_uses_new_route(managed_families, monkeypatch):
    from django_core_micha.assistant import turns

    user = _user("running-family-turn")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra",
        selected_driver="openai_api", selected_model="astra-v1", selected_source_id="openai-main", selected_effort="low")
    seen = []

    class FakeDriver:
        async def stream(self, **kwargs):
            seen.append((kwargs["model"], kwargs["source_id"], kwargs["billing_reference"]))
            from asgiref.sync import sync_to_async
            await sync_to_async(apply_policy)(policy_payload(revision=2, astra_source="openai-other", astra_model="astra-v2"))
            yield {"type": "usage", "usage": {"input_tokens": 12, "output_tokens": 4,
                "cache_read_tokens": 3, "cache_write_tokens": 1, "reported": True}}
            yield {"type": "delta", "text": "synthetic answer"}

    monkeypatch.setattr(turns, "_make_driver", lambda *_args: FakeDriver())
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_args: "synthetic system")
    monkeypatch.setattr(turns, "build_turn_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_args: [])
    monkeypatch.setattr(turns, "prompt_version", lambda: "")
    frames = []

    async def emit(frame):
        frames.append(frame)

    result = async_to_sync(turns.run_turn)(chat, user, "synthetic question", emit)
    assert seen == [("astra-v1", "openai-main", str(result.pk))]
    assert (result.provider, result.family, result.source_id, result.model) == (
        "openai", "astra", "openai-main", "astra-v1")
    record = AICostRecord.objects.get(purpose="chat", reference=str(result.pk))
    assert record.source_id == "openai-main" and record.model == "astra-v1"
    public_turn = frames[-1]["turn"]
    assert public_turn["provider"] == "openai" and public_turn["family"] == "astra"
    assert public_turn["usage"]["input_tokens"] == 12
    assert not {"source_id", "source_label", "model", "driver", "billing_source", "cost_usd_micros"} & public_turn.keys()
    assert "cost_usd_micros" not in public_turn["usage"]
    next_turn = __import__("django_core_micha.assistant.turns", fromlist=["_start"])._start(chat.pk, user.pk, "next")
    assert (next_turn.source_id, next_turn.model) == ("openai-other", "astra-v2")


def test_queued_turn_rechecks_the_current_family_route_before_dispatch(managed_families, monkeypatch):
    from django_core_micha.assistant import turns

    user = _user("queued-family-turn")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra",
        selected_driver="openai_api", selected_model="astra-v1", selected_source_id="openai-main", selected_effort="low")
    queued = turns._start(chat.pk, user.pk, "queued before route update")
    apply_policy(policy_payload(revision=2, astra_source="openai-other", astra_model="astra-v2"))
    monkeypatch.setattr(turns, "_start", lambda *_args: queued)
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_args: "synthetic system")
    monkeypatch.setattr(turns, "build_turn_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_args: [])
    monkeypatch.setattr(turns, "prompt_version", lambda: "")
    called = []

    class FakeDriver:
        async def stream(self, **kwargs):
            called.append((kwargs["model"], kwargs["source_id"]))
            yield {"type": "usage", "usage": {"input_tokens": 4, "output_tokens": 2, "reported": True}}
            yield {"type": "delta", "text": "queued answer"}

    monkeypatch.setattr(turns, "_make_driver", lambda *_args: FakeDriver())
    async def emit(_frame):
        return None
    completed = async_to_sync(turns.run_turn)(chat, user, "queued before route update", emit)
    assert called == [("astra-v2", "openai-other")]
    assert completed.source_id == "openai-other" and completed.model == "astra-v2"


def test_completion_and_direct_workloads_share_family_resolution_and_do_not_fallback(managed_families, monkeypatch):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.base import DriverError
    from django_core_micha.assistant.catalogue import entries

    user = _user("family-completion")
    calls = []

    class FakeDriver:
        async def stream(self, **kwargs):
            calls.append((kwargs["model"], kwargs["source_id"], kwargs["billing_reference"]))
            yield {"type": "usage", "usage": {"input_tokens": 9, "output_tokens": 3, "reported": True}}
            yield {"type": "delta", "text": "synthetic completion"}

    monkeypatch.setattr(completion, "_make_driver", lambda *_args: FakeDriver())
    result = async_to_sync(completion.acomplete)(user, prompt="synthetic", provider="claude",
        family="sonnet", effort="low", reference="completion-family-1")
    assert result.text == "synthetic completion" and result.usage_reported
    assert not hasattr(result, "model") and not hasattr(result, "source_id")
    assert calls == [("sonnet-v1", "claude-api", "completion-family-1")]
    cost = AICostRecord.objects.get(reference="completion-family-1", purpose="completion")
    assert cost.source_id == "claude-api" and cost.user_id == user.pk

    extraction = resolve_workload(user, rows=entries(), provider="openai", family="luna",
        workload="extraction", refresh=False)
    assert extraction.source_id == "openai-main" and extraction.model == "luna-v1"
    with pytest.raises(DriverError):
        async_to_sync(completion.acomplete)(user, prompt="synthetic", provider="openai", family="terra",
            effort="low", purpose="extraction")

    apply_policy(policy_payload(revision=2, omit=("sonnet",)))
    with pytest.raises(DriverError):
        async_to_sync(completion.acomplete)(user, prompt="no fallback", provider="claude",
            family="sonnet", effort="low")
    assert len(calls) == 1


def test_usage_normalization_late_settlement_and_tariff_ignore_upstream_source(managed_families):
    from django_core_micha.billing.models import TokenEntry
    from django_core_micha.billing.services import debit_tokens, grant_tokens, record_ai_call
    from django_core_micha.assistant.turns import _finish_and_hook
    from django_core_micha.extraction.drivers._common import usage_from_response

    openai_usage = usage_from_response(NS(usage=NS(input_tokens=100, output_tokens=20,
        input_tokens_details=NS(cached_tokens=25))))
    claude_usage = usage_from_response(NS(usage=NS(input_tokens=70, output_tokens=10,
        cache_read_input_tokens=20, cache_creation_input_tokens=10)))
    assert (openai_usage.input_tokens, openai_usage.cache_read_tokens, openai_usage.total_tokens) == (75, 25, 120)
    assert (claude_usage.input_tokens, claude_usage.cache_read_tokens, claude_usage.cache_write_tokens,
            claude_usage.total_tokens) == (70, 20, 10, 110)
    assert usage_from_response(NS(usage=NS())).reported is False

    user = _user("family-accounting")
    grant_tokens(user, 100, reference="family-credit", purpose="test")
    first = record_ai_call(user=user, provider="anthropic_api", model="claude-sonnet-5-5",
        input_tokens=70, output_tokens=10, cache_read_tokens=20, cache_write_tokens=10,
        source_id="claude-api", purpose="chat", reference="api-route-1")
    second = record_ai_call(user=user, provider="claude_cli", model="claude-sonnet-5-5",
        input_tokens=70, output_tokens=10, cache_read_tokens=20, cache_write_tokens=10,
        source_id="claude-sub", purpose="chat", reference="subscription-route-1")
    assert first.input_tokens == second.input_tokens == 70
    assert first.cache_read_tokens == second.cache_read_tokens == 20
    assert first.cache_write_tokens == second.cache_write_tokens == 10
    assert first.cost_usd_micros > 0 and second.cost_usd_micros == 0
    assert (first.source_id, second.source_id) == ("claude-api", "claude-sub")

    unresolved = record_ai_call(user=user, provider="claude_cli", model="claude-sonnet-5-5",
        source_id="claude-sub", purpose="completion", reference="late-usage-1", usage_reported=False)
    assert unresolved.usage_status == "unresolved" and unresolved.cost_usd_micros is None
    settled = record_ai_call(user=user, provider="claude_cli", model="claude-sonnet-5-5",
        input_tokens=5, output_tokens=2, cache_read_tokens=1, source_id="claude-sub",
        purpose="completion", reference="late-usage-1", usage_reported=True)
    assert settled.pk == unresolved.pk and settled.usage_status == "reported" and settled.input_tokens == 5
    assert record_ai_call(user=user, provider="claude_cli", model="claude-sonnet-5-5",
        input_tokens=7, output_tokens=2, source_id="different-source", purpose="completion",
        reference="late-usage-1", usage_reported=True) is None

    grant_tokens(user, 500, reference="same-tariff-credit", purpose="test")
    hook_calls = []
    def usage_hook(_chat, owner, turn, reported):
        amount = sum(reported.get(key, 0) for key in
                     ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
        hook_calls.append((owner.pk, amount, turn.source_id))
        debit_tokens(owner, amount, purpose="chat", reference=str(turn.pk))

    first_turn = None
    for index, (driver, source) in enumerate((("anthropic_api", "claude-api"), ("claude_cli", "claude-sub")), start=1):
        if index == 2:
            apply_policy(policy_payload(revision=2, sonnet_source="claude-sub", sonnet_model="sonnet-sub-v1"))
        chat = AssistantChat.objects.create(owner=user, selected_provider="claude", selected_family="sonnet")
        turn = AssistantTurn.objects.create(chat=chat, sender=user, sequence=1, user_text="synthetic",
            driver=driver, model="claude-sonnet-5-5", provider="claude", family="sonnet",
            source_id=source, provider_started=True)
        if index == 1:
            first_turn = turn
        with override_settings(DCM_ASSISTANT_USAGE_HOOK=usage_hook):
            async_to_sync(_finish_and_hook)(turn.pk, "answer", "completed",
                usage={"input_tokens": 70, "output_tokens": 10, "cache_read_tokens": 20, "cache_write_tokens": 10},
                usage_reported=True)
            if index == 1:
                async_to_sync(_finish_and_hook)(turn.pk, "duplicate", "completed",
                    usage={"input_tokens": 70, "output_tokens": 10, "cache_read_tokens": 20, "cache_write_tokens": 10},
                    usage_reported=True)
    debits = list(TokenEntry.objects.filter(account__user=user, kind="debit").values_list("amount", flat=True))
    assert debits == [-110, -110]
    assert hook_calls == [(user.pk, 110, "claude-api"), (user.pk, 110, "claude-sub")]
    assert AICostRecord.objects.get(purpose="chat", reference=str(first_turn.pk)).source_id == "claude-api"


def test_unknown_extraction_usage_stays_unresolved_and_source_attributed(managed_families, monkeypatch):
    from django_core_micha.extraction import service
    from django_core_micha.extraction.types import ExtractionRequest, ExtractionResult, TokenUsage

    user = _user("family-extraction-usage")
    monkeypatch.setattr(service.openai_driver, "extract", lambda *_args, **_kwargs:
        ExtractionResult("{}", NS(usage=None), TokenUsage(reported=False)))
    request = ExtractionRequest(system_prompt="synthetic", user_prompt="synthetic", schema={"type": "object"},
        max_output_tokens=10, effort="low", thinking={})
    with override_settings(AI_COST_LIMIT_ENABLED=False):
        result = service.extract_document(request, provider="openai", api_key="synthetic", model="astra-v1",
            user_id=user.pk, source_id="openai-main", reference="extraction-family-1")
    assert result.usage.reported is False
    record = AICostRecord.objects.get(reference="extraction-family-1", purpose="extraction")
    assert record.usage_status == "unresolved" and record.cost_usd_micros is None
    assert record.source_id == "openai-main" and record.user_id == user.pk


def test_missing_chat_usage_is_unresolved_not_zero_or_a_user_debit(managed_families):
    from django_core_micha.assistant.turns import _finish_and_hook, turn_payload

    user = _user("family-missing-chat-usage")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra")
    turn = AssistantTurn.objects.create(chat=chat, sender=user, sequence=1, user_text="synthetic",
        driver="openai_api", model="astra-v1", provider="openai", family="astra",
        source_id="openai-main", provider_started=True)
    hook_calls = []
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=lambda *_args: hook_calls.append(True)):
        async_to_sync(_finish_and_hook)(turn.pk, "failed", "failed", "provider_error",
            usage={}, usage_reported=False)
    turn.refresh_from_db()
    record = AICostRecord.objects.get(reference=str(turn.pk), purpose="chat")
    assert not turn.usage_reported and turn.cost_usd_micros == 0
    assert record.usage_status == "unresolved" and record.cost_usd_micros is None
    assert record.source_id == "openai-main" and hook_calls == []
    assert turn_payload(turn)["usage"] is None


def test_execution_eligibility_uses_resolved_pair_and_denies_before_dataset_authorization(managed_families):
    from django_core_micha.assistant.execution import Dataset, model_supports_execution, prepare_execution

    apply_policy(policy_payload(revision=2, execution=True))
    user = _user("family-execution")
    resolved = resolve_workload(user, rows=catalogue_rows(), provider="openai", family="astra",
        workload="chat", refresh=False)
    assert model_supports_execution(resolved.row)
    checker = lambda source, driver, model, tool: (source, driver, model, tool) == (
        "openai-main", "openai_api", "astra-v1", "code_interpreter")
    authorized_datasets = []
    dataset = {"name": "synthetic.csv", "data_class": "synthetic", "content": b"a,b\n1,2\n",
        "media_type": "text/csv"}
    async def authorize():
        return True
    def execution_policy(*_args):
        authorized_datasets.append(True)
        return {"enabled": True, "datasets": [dataset]}
    chat = NS(pk=10, owner_id=user.pk, scope="private")

    class WrongRoute:
        driver = "openai_api"
        model = resolved.model
        source_id = "openai-other"

    with override_settings(DCM_ASSISTANT_EXECUTION_POLICY=execution_policy,
                           DCM_AI_EXECUTION_CAPABILITY_CHECK=checker):
        assert model_supports_execution(resolved.row)
        assert not model_supports_execution({**resolved.row, "source_id": "openai-other"})
        assert not model_supports_execution({**resolved.row, "model": "unknown-model"})
        assert async_to_sync(prepare_execution)(chat, user, WrongRoute(), resolved.row,
            authorize=authorize) is None
        assert authorized_datasets == []
        turn = NS(pk=11, driver=resolved.driver, model=resolved.model, source_id=resolved.source_id)
        prepared = async_to_sync(prepare_execution)(chat, user, turn, resolved.row, authorize=authorize)
    assert prepared is not None and prepared.datasets[0].content == dataset["content"]
    sonnet = resolve_workload(user, rows=catalogue_rows(), provider="claude", family="sonnet",
        workload="chat", refresh=False)
    assert "code_execution" not in sonnet.capabilities
    with pytest.raises(ValueError, match="invalid_selection"):
        resolve_workload(user, rows=catalogue_rows(), provider="claude", family="sonnet",
            workload="code_execution", refresh=False)
    apply_policy(policy_payload(revision=3, execution=True, astra_source="openai-other", astra_model="astra-v2"))
    changed = resolve_workload(user, rows=catalogue_rows(), provider="openai", family="astra", workload="chat", refresh=False)
    with override_settings(DCM_AI_EXECUTION_CAPABILITY_CHECK=lambda source, driver, model, tool:
                           (source, driver, model, tool) == ("openai-other", "openai_api", "astra-v2", "code_interpreter")):
        assert model_supports_execution(changed.row)
    assert not model_supports_execution(resolved.row)


def test_unmanaged_catalogue_contract_remains_available():
    user = _user("unmanaged-compatible")
    rows = [{"driver": "openai_api", "model": "legacy-model", "name": "Legacy model",
        "efforts": ["low"], "billing": "pay_per_use", "default": True}]
    with override_settings(DCM_AI_POLICY_ENABLED=False, DCM_ASSISTANT_CATALOGUE=rows,
                           OPENAI_API_KEY="synthetic-legacy-key"):
        response = _catalogue(user)
    assert response["entries"][0]["driver"] == "openai_api"
    assert response["entries"][0]["model"] == "legacy-model"


def test_failed_turn_with_partial_usage_stays_unresolved(managed_families):
    from django_core_micha.assistant.turns import _finish_and_hook
    from django_core_micha.billing.services import record_ai_call

    user = _user("partial-usage")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra")
    turn = AssistantTurn.objects.create(chat=chat, sender=user, sequence=1, user_text="synthetic",
        driver="openai_api", model="astra-v1", source_id="openai-main", provider_started=True)
    record_ai_call(user=user, provider=turn.driver, model=turn.model, source_id=turn.source_id,
                   purpose="chat", reference=str(turn.pk), usage_reported=False)
    hooks = []
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=lambda *args: hooks.append(args)):
        async_to_sync(_finish_and_hook)(turn.pk, "partial", "failed", "provider_error",
            usage={"input_tokens": 10, "output_tokens": 2}, usage_reported=True)
    turn.refresh_from_db()
    assert turn.usage_reported is False
    assert AICostRecord.objects.get(purpose="chat", reference=str(turn.pk)).usage_status == "unresolved"
    assert hooks == []


def test_late_workload_usage_settles_once_with_app_tariff(managed_families):
    from django_core_micha.billing.models import TokenEntry
    from django_core_micha.billing.services import debit_tokens, grant_tokens, record_ai_call, settle_ai_usage

    user = _user("late-workload-tariff")
    grant_tokens(user, 100, reference="workload-credit", purpose="test")
    def app_tariff(owner, record):
        amount = record.input_tokens + record.output_tokens + record.cache_read_tokens + record.cache_write_tokens
        debit_tokens(owner, amount, purpose=record.purpose, reference=record.operation_reference)

    args = dict(user=user, provider="claude_cli", model="sonnet-v1", source_id="claude-sub",
                purpose="completion", reference="late-workload")
    pending = record_ai_call(**args, usage_reported=False)
    assert pending.usage_status == "unresolved" and pending.settlement_status == "pending"
    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=app_tariff):
        settled = record_ai_call(**args, input_tokens=12, output_tokens=3, cache_read_tokens=2)
        assert settled.settlement_status == "settled"
        assert settle_ai_usage(settled.pk)
        assert record_ai_call(**args, input_tokens=12, output_tokens=3, cache_read_tokens=2).pk == settled.pk
    assert list(TokenEntry.objects.filter(account__user=user, kind="debit").values_list("amount", flat=True)) == [-17]


def test_late_chat_usage_runs_existing_tariff_once(managed_families):
    from django_core_micha.billing.models import TokenEntry
    from django_core_micha.billing.services import debit_tokens, grant_tokens, record_ai_call, settle_ai_usage

    user = _user("late-chat-tariff")
    grant_tokens(user, 100, reference="late-chat-credit", purpose="test")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra")
    turn = AssistantTurn.objects.create(chat=chat, sender=user, sequence=1, user_text="synthetic", state="completed",
        driver="openai_api", model="astra-v1", source_id="openai-main", provider_started=True)
    args = dict(user=user, provider=turn.driver, model=turn.model, source_id=turn.source_id,
                purpose="chat", reference=str(turn.pk))
    unresolved = record_ai_call(**args, usage_reported=False)
    assert unresolved.settlement_status == "pending"
    def app_tariff(_chat, owner, saved_turn, usage):
        debit_tokens(owner, usage["input_tokens"] + usage["output_tokens"],
                     purpose="chat", reference=f"assistant:{saved_turn.pk}")
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=app_tariff):
        settled = record_ai_call(**args, input_tokens=12, output_tokens=3)
        assert settled.settlement_status == "settled"
        assert settle_ai_usage(settled.pk)
    turn.refresh_from_db()
    assert turn.usage_reported and turn.usage["input_tokens"] == 12
    assert list(TokenEntry.objects.filter(account__user=user, kind="debit").values_list("amount", flat=True)) == [-15]


def test_failed_chat_tariff_stays_pending_for_retry(managed_families):
    from django_core_micha.assistant.turns import _finish_and_hook
    from django_core_micha.billing.services import debit_tokens, grant_tokens, settle_ai_usage
    user = _user("retry-chat-tariff")
    grant_tokens(user, 100, reference="retry-chat-credit", purpose="test")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra")
    turn = AssistantTurn.objects.create(chat=chat, sender=user, sequence=1, user_text="synthetic",
        driver="openai_api", model="astra-v1", source_id="openai-main", provider_started=True)
    def broken(*_args):
        raise RuntimeError("synthetic tariff failure")
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=broken):
        async_to_sync(_finish_and_hook)(turn.pk, "answer", "completed",
            usage={"input_tokens": 12, "output_tokens": 3}, usage_reported=True)
    record = AICostRecord.objects.get(purpose="chat", reference=str(turn.pk))
    assert record.settlement_status == "pending"
    def tariff(_chat, owner, saved_turn, usage):
        debit_tokens(owner, usage["input_tokens"] + usage["output_tokens"],
                     purpose="chat", reference=f"assistant:{saved_turn.pk}")
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=tariff):
        assert settle_ai_usage(record.pk)
    record.refresh_from_db()
    assert record.settlement_status == "settled"


def test_completion_tariff_is_same_across_sources_and_supports_refund(managed_families, monkeypatch):
    from django_core_micha.assistant import completion
    from django_core_micha.billing.models import TokenEntry
    from django_core_micha.billing.services import debit_tokens, grant_tokens, reverse_debit

    user = _user("completion-source-tariff")
    grant_tokens(user, 100, reference="completion-source-credit", purpose="test")
    class Driver:
        async def stream(self, **kwargs):
            yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 3}}
            yield {"type": "delta", "text": "answer"}
    monkeypatch.setattr(completion, "_make_driver", lambda *_: Driver())
    def tariff(owner, record):
        debit_tokens(owner, record.input_tokens + record.output_tokens, purpose="completion",
                     reference=record.operation_reference)
    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=tariff):
        async_to_sync(completion.acomplete)(user, prompt="one", provider="claude", family="sonnet",
                                            effort="low", reference="api-completion")
        apply_policy(policy_payload(revision=2, sonnet_source="claude-sub", sonnet_model="sonnet-sub-v1"))
        async_to_sync(completion.acomplete)(user, prompt="two", provider="claude", family="sonnet",
                                            effort="low", reference="subscription-completion")
        reverse_debit(user, reference="subscription-completion", reason="test refund")
    entries = TokenEntry.objects.filter(account__user=user)
    assert sorted(entries.filter(kind="debit").values_list("amount", flat=True)) == [-13, -13]
    assert entries.filter(kind="adjustment", reference="reversal:subscription-completion", amount=13).exists()
    assert AICostRecord.objects.get(reference="api-completion").source_id == "claude-api"
    assert AICostRecord.objects.get(reference="subscription-completion").source_id == "claude-sub"


def test_completion_with_unreported_repair_stays_unresolved(managed_families, monkeypatch):
    from django_core_micha.assistant import completion
    user = _user("completion-partial-usage")
    calls = []
    class Driver:
        async def stream(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 2}}
                yield {"type": "delta", "text": "invalid json"}
            else:
                yield {"type": "usage", "usage": {}, "usage_reported": False}
                yield {"type": "delta", "text": '{"ok": true}'}
    monkeypatch.setattr(completion, "_make_driver", lambda *_: Driver())
    charges = []
    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=lambda *args: charges.append(args)):
        result = async_to_sync(completion.acomplete)(user, prompt="test", provider="openai", family="astra",
            effort="low", reference="partial-completion", response_schema={"type":"object", "properties":{"ok":{"type":"boolean"}}, "required":["ok"], "additionalProperties":False})
    record = AICostRecord.objects.get(reference="partial-completion")
    assert len(calls) == 2 and result.usage_reported is False
    assert record.usage_status == "unresolved" and record.settlement_status == "pending" and charges == []


def test_missing_workload_tariff_adapter_prevents_provider_dispatch(managed_families, monkeypatch):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.base import DriverError
    called = []
    monkeypatch.setattr(completion, "_make_driver", lambda *_: called.append(True))
    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=None):
        with pytest.raises(DriverError, match="accounting_unavailable"):
            async_to_sync(completion.acomplete)(_user("missing-tariff"), prompt="test", provider="openai",
                                                family="astra", effort="low")
    assert called == []


def test_chat_with_unreported_provider_round_stays_unresolved(managed_families, monkeypatch):
    from django_core_micha.assistant import turns
    user = _user("chat-partial-round")
    chat = AssistantChat.objects.create(owner=user, selected_provider="openai", selected_family="astra",
        selected_driver="openai_api", selected_model="astra-v1", selected_source_id="openai-main", selected_effort="low")
    class Driver:
        async def stream(self, **kwargs):
            yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 2}}
            yield {"type": "usage", "usage": {}, "usage_reported": False}
            yield {"type": "delta", "text": "answer"}
    monkeypatch.setattr(turns, "_make_driver", lambda *_: Driver())
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "synthetic system")
    monkeypatch.setattr(turns, "build_turn_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_: [])
    monkeypatch.setattr(turns, "prompt_version", lambda: "")
    async def emit(_frame):
        return None
    charges = []
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=lambda *args: charges.append(args)):
        turn = async_to_sync(turns.run_turn)(chat, user, "question", emit)
    record = AICostRecord.objects.get(purpose="chat", reference=str(turn.pk))
    assert not turn.usage_reported and record.usage_status == "unresolved"
    assert record.settlement_status == "pending" and charges == []
