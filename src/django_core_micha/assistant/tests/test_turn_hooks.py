import asyncio
from types import SimpleNamespace as NS

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from unittest.mock import AsyncMock

from django_core_micha.assistant.consumers import ChatConsumer
from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.hooks import AfterTurnResult
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def catalogue():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"gpt-test","name":"test","efforts":["low","medium"],"billing":"pay_per_use","default":True}]): yield

@pytest.fixture
def user(): return get_user_model().objects.create_user(username="hooks-user", password="secret")

@pytest.fixture
def chat(user): return AssistantChat.objects.create(owner=user, selected_driver="openai_api", selected_model="gpt-test")

def _run(monkeypatch, chat, user, driver, frames):
    from django_core_micha.assistant import turns
    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "tools_for_chat", lambda chat: [])
    monkeypatch.setattr(turns, "_make_driver", lambda *_: driver)
    async def emit(frame): frames.append(frame)
    async def run(): return await turns.run_turn(chat, user, "question", emit)
    return async_to_sync(run)

def test_context_is_for_one_turn_and_receives_turn_id(monkeypatch, chat, user):
    seen=[]
    def context(c,u,m,t): seen.append((c.pk,u.pk,m,t.pk)); return "private context"
    class Driver:
        async def stream(self, **kwargs):
            seen.append(kwargs); yield {"type":"delta","text":"ok"}
    frames=[]
    with override_settings(DCM_ASSISTANT_TURN_CONTEXT=context):
        runner=_run(monkeypatch,chat,user,Driver(),frames)
        runner(); runner()
    assert seen[0][:3] == (chat.pk,user.pk,"question")
    assert seen[0][3]
    assert seen[1]["messages"][-1]["content"] == "private context\n\n---\n\nquestion"
    assert all("private context" not in row.user_text for row in AssistantTurn.objects.filter(chat=chat))
    # Older history on the next turn carries neither its prior context nor any context record.
    assert len(seen[3]["messages"]) >= 3
    assert all("private context" not in row["content"] for row in seen[3]["messages"][:-1])

def test_context_failure_finishes_without_provider_call(monkeypatch, chat, user):
    called=[]
    def fail(*_): raise RuntimeError("private failure")
    class Driver:
        async def stream(self, **kwargs): called.append(True); yield {"type":"delta","text":"no"}
    frames=[]
    with override_settings(DCM_ASSISTANT_TURN_CONTEXT=fail):
        with pytest.raises(DriverError) as exc: _run(monkeypatch,chat,user,Driver(),frames)()
    row=AssistantTurn.objects.get(chat=chat)
    assert exc.value.code == "provider_error" and row.state == "failed" and called == []

def test_after_turn_replaces_text_and_emits_frames_before_final(monkeypatch, chat, user):
    class Driver:
        async def stream(self, **kwargs): yield {"type":"delta","text":"raw"}
    frames=[]
    hook=lambda c,u,t,text: AfterTurnResult("replacement", [{"name":"proposals","payload":{"items":[]}}])
    with override_settings(DCM_ASSISTANT_AFTER_TURN=hook): _run(monkeypatch,chat,user,Driver(),frames)()
    row=AssistantTurn.objects.get(chat=chat)
    assert row.assistant_text == "replacement"
    assert frames[-2]["type"] == "app" and frames[-2]["name"] == "proposals"
    assert frames[-2]["payload"] == {"items":[]} and frames[-1]["type"] == "turn"
    assert frames[-1]["turn"]["assistant_text"] == "replacement"

def test_after_turn_failure_keeps_raw_completed_answer(monkeypatch, chat, user):
    class Driver:
        async def stream(self, **kwargs): yield {"type":"delta","text":"raw"}
    frames=[]
    def fail(*_): raise RuntimeError("hook failure")
    with override_settings(DCM_ASSISTANT_AFTER_TURN=fail): _run(monkeypatch,chat,user,Driver(),frames)()
    row=AssistantTurn.objects.get(chat=chat)
    assert row.state == "completed" and row.assistant_text == "raw"

def test_unavailable_send_returns_reason_without_creating_turn(chat, user):
    consumer=ChatConsumer(); consumer.chat=chat; consumer.scope={"user":user}; consumer.task=None
    consumer.send_json=AsyncMock()
    async def deny(u,s): return "approval_required"
    consumer._availability=deny
    with override_settings(DCM_ASSISTANT_AVAILABILITY=lambda u,s:"approval_required"):
        async_to_sync(consumer.receive_json)({"type":"send","message":"hello"})
    assert consumer.send_json.await_args.args[0] == {"type":"error","code":"unavailable","reason":"approval_required"}
    assert AssistantTurn.objects.filter(chat=chat).count() == 0


def test_cli_turn_gets_context_in_prompt_data_and_unset_prompt_version_keeps_the_stored_one(monkeypatch, user):
    import json

    rows = [{"driver": "claude_cli", "model": "cli-test", "name": "cli", "efforts": ["low", "medium"], "billing": "subscription", "default": True}]
    seen = {}

    class CliDriver:
        async def stream(self, **kwargs):
            seen.update(kwargs)
            yield {"type": "init"}
            yield {"type": "delta", "text": "ok"}
            yield {"type": "session", "session_id": "s-1"}

    with override_settings(DCM_ASSISTANT_CATALOGUE=rows, DCM_ASSISTANT_TURN_CONTEXT=lambda c, u, m, t: "CTX-TEXT"):
        cli_chat = AssistantChat.objects.create(owner=user, selected_driver="claude_cli", selected_model="cli-test",
            session_prompt_version="v1")
        _run(monkeypatch, cli_chat, user, CliDriver(), [])()
    data = json.loads(seen["prompt"])
    assert data["context"] == "CTX-TEXT" and data["message"] == "question"
    assert "CTX-TEXT" not in AssistantTurn.objects.get(chat=cli_chat).user_text
    cli_chat.refresh_from_db()
    assert cli_chat.session_reference == "s-1" and cli_chat.session_prompt_version == "v1"
