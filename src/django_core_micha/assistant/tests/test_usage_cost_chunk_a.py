import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.ai_prices import price_usage
from django_core_micha.assistant.drivers.anthropic_api import AnthropicDriver
from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
from django_core_micha.assistant.drivers.claude_cli import ClaudeCliDriver
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(("model", "usage", "expected"), [
    ("claude-fable-5-1", (100, 100, 10, 10), 6128),
    ("claude-opus-5-5", (100, 100, 10, 10), 400 + 2000 + 2 + 50),
    ("claude-sonnet-5-5", (100, 100, 10, 10), 200 + 1000 + 1 + 25),
    ("gpt-6-astra", (100, 100, 10, 10), 1000 + 5000 + 100 + 100),
    ("gpt-6.1-sol", (100, 100, 10, 10), 200 + 1000 + 1 + 20),
    ("gpt-6-luna", (100, 100, 10, 10), 62),
])
def test_default_price_table(model, usage, expected):
    with override_settings():
        assert price_usage(model, input_tokens=usage[0], output_tokens=usage[1],
                           cache_read_tokens=usage[2], cache_write_tokens=usage[3]) == expected


def test_haiku_tier_switches_only_above_100k_prompt_tokens():
    assert price_usage("claude-haiku-5-5", input_tokens=100000, output_tokens=1) == 10001
    assert price_usage("claude-haiku-5-5", input_tokens=100001, output_tokens=1) == 50003


def test_price_missing_cache_rates_settings_override_and_legacy_fallback():
    with override_settings(AI_MODEL_PRICES_USD_PER_MILLION={"custom": {"input": 2, "output": 3}},
                          AI_MODEL_COST_TABLE_CENTS_PER_1K={"legacy": {"input": 0.2, "output": 0.8}}):
        assert price_usage("custom", input_tokens=10, output_tokens=10, cache_read_tokens=10, cache_write_tokens=10) == 90
        assert price_usage("legacy", input_tokens=1000, output_tokens=1000) == 10000
        assert price_usage("unknown", input_tokens=1000, output_tokens=1000) == 75000
    with override_settings(AI_MODEL_PRICES_USD_PER_MILLION={"custom": {"input": 1, "output": 2,
                                                                            "cache_read": 0.5, "cache_write": 0.25}}):
        assert price_usage("custom", input_tokens=1, output_tokens=1, cache_read_tokens=1, cache_write_tokens=1) == 4


class _OpenAIStream:
    def __aiter__(self):
        async def events():
            yield NS(type="response.completed", response=NS(
                usage=NS(input_tokens=120, output_tokens=7, input_tokens_details=NS(cached_tokens=30)), output=[]))
        return events()


class _AnthropicStream:
    class Texts:
        def __aiter__(self):
            async def texts():
                if False: yield ""
            return texts()
    text_stream = Texts()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def get_final_message(self):
        return NS(usage=NS(input_tokens=11, output_tokens=3, cache_read_input_tokens=5,
                           cache_creation_input_tokens=2), content=[])


def test_openai_driver_splits_cached_input_tokens():
    class Responses:
        async def create(self, **kwargs): return _OpenAIStream()
    driver = OpenAIDriver(NS(responses=Responses()))
    async def run(): return [event async for event in driver.stream(model="gpt-6-astra", messages=[])]
    usage = next(event["usage"] for event in async_to_sync(run)() if event["type"] == "usage")
    assert usage == {"input_tokens": 90, "output_tokens": 7, "cache_read_tokens": 30, "cache_write_tokens": 0}


def test_anthropic_driver_maps_cache_fields():
    class Messages:
        def stream(self, **kwargs): return _AnthropicStream()
    driver = AnthropicDriver(NS(messages=Messages()))
    async def run(): return [event async for event in driver.stream(model="claude-sonnet-5-5", messages=[])]
    usage = next(event["usage"] for event in async_to_sync(run)() if event["type"] == "usage")
    assert usage == {"input_tokens": 11, "output_tokens": 3, "cache_read_tokens": 5, "cache_write_tokens": 2}


def _cli_driver(monkeypatch, rows):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "claude")
    class Input:
        def write(self, _data): pass
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    class Output:
        def __init__(self): self.lines = iter([(json.dumps(row) + "\n").encode() for row in rows])
        async def readline(self): return next(self.lines, b"")
    class Proc:
        pid = 123
        returncode = None
        def __init__(self):
            self.stdin, self.stdout = Input(), Output()
            self.stderr = NS(read=AsyncMock(return_value=b""))
        async def wait(self): self.returncode = 0; return 0
    proc = Proc()
    async def factory(*_args, **_kwargs): return proc
    return ClaudeCliDriver(process_factory=factory, kill_tree=lambda _proc: None)


@pytest.mark.parametrize(("assistant_usage", "result_usage", "expected"), [
    ({"input_tokens": 8, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4},
     {"input_tokens": 99, "output_tokens": 99},
     {"input_tokens": 8, "output_tokens": 2, "cache_read_tokens": 4, "cache_write_tokens": 3, "context_tokens": 15}),
    (None, {"input_tokens": 8, "output_tokens": 2, "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4},
     {"input_tokens": 8, "output_tokens": 2, "cache_read_tokens": 4, "cache_write_tokens": 3}),
    (None, None, {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}),
])
def test_claude_cli_maps_result_usage_or_records_zero(monkeypatch, tmp_path, assistant_usage, result_usage, expected):
    rows = [{"type": "system", "subtype": "init", "tools": [], "mcp_servers": []}]
    if assistant_usage is not None:
        rows.append({"type": "assistant", "message": {"usage": assistant_usage, "content": []}})
    result = {"type": "result"}
    if result_usage is not None: result["usage"] = result_usage
    rows.append(result)
    driver = _cli_driver(monkeypatch, rows)
    async def run(): return [event async for event in driver.stream(prompt="p", model="claude-fable-5-1", tools=[],
        credential="token", chat=NS(pk="cli-usage"))]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        events = async_to_sync(run)()
    assert next(event["usage"] for event in events if event["type"] == "usage") == expected


@pytest.fixture
def user(): return get_user_model().objects.create_user(username="usage-cost-user", password="secret")


@pytest.fixture
def chat(user):
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver": "openai_api", "model": "priced-test", "name": "Test",
            "efforts": ["low", "medium"], "billing": "pay_per_use", "default": True}]):
        yield AssistantChat.objects.create(owner=user, selected_driver="openai_api", selected_model="priced-test")


def test_turn_prices_all_rounds_saves_payload_and_notifies_hook(monkeypatch, chat, user):
    from django_core_micha.assistant import turns
    from django_core_micha.assistant.hooks import AfterTurnResult
    from django_core_micha.assistant.turns import turn_payload
    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_: [])
    calls = []
    class Driver:
        async def stream(self, **_kwargs):
            for _ in range(2 if len(calls) == 0 else 1):
                yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 2,
                    "cache_read_tokens": 5, "cache_write_tokens": 1}}
                if len(calls) == 0:
                    yield {"type": "tool", "phase": "end", "name": "lookup", "label": "Lookup", "sources": []}
            yield {"type": "delta", "text": "answer"}
    monkeypatch.setattr(turns, "_make_driver", lambda *_: Driver())
    def repair(_chat, _user, _turn, _text):
        calls.append(1)
        return AfterTurnResult(repair="fix") if len(calls) == 1 else None
    hook_rows = []
    def usage_hook(c, u, t, usage): hook_rows.append((c.pk, u.pk, usage.copy()))
    frames = []
    async def emit(frame): frames.append(frame)
    async def run(): return await turns.run_turn(chat, user, "question", emit)
    with override_settings(DCM_ASSISTANT_AFTER_TURN=repair, DCM_ASSISTANT_USAGE_HOOK=usage_hook,
                           AI_MODEL_PRICES_USD_PER_MILLION={"priced-test": {"input": 2, "output": 3,
                               "cache_read": 1, "cache_write": 4}}):
        async_to_sync(run)()
    turn = AssistantTurn.objects.get(chat=chat)
    expected = {"input_tokens": 30, "output_tokens": 6, "cache_read_tokens": 15, "cache_write_tokens": 3,
                "cost_usd_micros": 105, "context_tokens": 16}
    assert turn.usage == expected and turn.cost_usd_micros == 105 and turn.context_tokens == 16
    assert turn_payload(turn)["usage"] == expected
    assert frames[-1]["type"] == "turn" and frames[-1]["turn"]["usage"] == expected
    assert hook_rows == [(chat.pk, user.pk, {"kind": "reply", **expected})]


def test_usage_hook_failure_does_not_change_turn_and_absent_hook_is_noop(monkeypatch, chat, user, caplog):
    from django_core_micha.assistant import turns
    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_: [])
    class Driver:
        async def stream(self, **_kwargs):
            yield {"type": "usage", "usage": {"input_tokens": 1, "output_tokens": 1}}
            yield {"type": "delta", "text": "ok"}
    monkeypatch.setattr(turns, "_make_driver", lambda *_: Driver())
    async def emit(_frame): pass
    async def run(): await turns.run_turn(chat, user, "question", emit)
    def fail(*_args): raise RuntimeError("sensitive detail")
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=fail): async_to_sync(run)()
    row = AssistantTurn.objects.get(chat=chat)
    assert row.state == "completed" and row.assistant_text == "ok"
    assert "usage hook failed (RuntimeError)" in caplog.text and "sensitive detail" not in caplog.text
    async_to_sync(run)()
    assert AssistantTurn.objects.filter(chat=chat, state="completed").count() == 2


def test_billing_record_ai_call_uses_new_price_and_legacy_cache_argument(user):
    from django_core_micha.billing.models import AICostRecord
    from django_core_micha.billing.services import record_ai_call
    with override_settings(AI_MODEL_PRICES_USD_PER_MILLION={"priced-test": {"input": 2, "output": 3,
                              "cache_read": 1, "cache_write": 4}}):
        record = record_ai_call(user=user, provider="openai_api", model="priced-test", input_tokens=10,
            output_tokens=2, cache_read_tokens=5, cache_write_tokens=1, purpose="chat")
        legacy = record_ai_call(user=user, provider="openai_api", model="priced-test", input_tokens=10,
            output_tokens=2, cache_tokens=6, purpose="chat")
    assert record.cost_usd_micros == 35 and record.cache_tokens == 6
    assert legacy.cost_usd_micros == 32 and legacy.cache_tokens == 6
    assert AICostRecord.objects.filter(user=user).count() == 2
