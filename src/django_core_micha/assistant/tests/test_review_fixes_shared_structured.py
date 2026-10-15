import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant import consumers, turns
from django_core_micha.assistant.consumers import ChatConsumer
from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def settings_shared():
    rows = [{"driver": "openai_api", "model": "m", "efforts": ["low"], "billing": "pay_per_use", "default": True}]
    permitted = {"allowed": True}
    with override_settings(DCM_ASSISTANT_CATALOGUE=rows, DCM_ASSISTANT_SHARED_SCOPE=lambda s: s == "shared",
                           DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: permitted["allowed"] or u.username != "editor"):
        yield permitted


@pytest.fixture
def owner(): return get_user_model().objects.create_user(username="owner", password="pw")


@pytest.fixture
def editor(): return get_user_model().objects.create_user(username="editor", password="pw")


@pytest.fixture
def chat(owner):
    return AssistantChat.objects.create(owner=owner, scope="shared", selected_driver="openai_api", selected_model="m", selected_effort="low")


def test_shared_scope_turn_frames_are_published_to_the_chat_group(chat, owner, monkeypatch):
    c = ChatConsumer(); c.chat = chat; c.scope = {"user": owner}; c.task = None
    c.send_json = AsyncMock(); c.shared_group = True; c.group_name = f"dcm_assistant_chat_{chat.pk}"
    c.channel_layer = NS(group_send=AsyncMock())
    async def fake_run_turn(chat_, user, message, emit, **kwargs):
        await emit({"type": "delta", "text": "hello"})
    monkeypatch.setattr(consumers, "run_turn", fake_run_turn)
    async def go():
        await c.receive_json({"type": "send", "message": "q"})
        await c.task
    async_to_sync(go)()
    c.channel_layer.group_send.assert_awaited_with(c.group_name, {"type": "chat.frame", "frame": {"type": "delta", "text": "hello"}})
    c.send_json.assert_not_awaited()  # nothing goes straight to the sender: delivery is through the group


def test_owner_only_scope_keeps_direct_delivery(owner, monkeypatch):
    plain = AssistantChat.objects.create(owner=owner, scope="", selected_driver="openai_api", selected_model="m")
    c = ChatConsumer(); c.chat = plain; c.scope = {"user": owner}; c.task = None
    c.send_json = AsyncMock(); c.shared_group = False
    async def fake_run_turn(chat_, user, message, emit, **kwargs):
        await emit({"type": "delta", "text": "mine"})
    monkeypatch.setattr(consumers, "run_turn", fake_run_turn)
    async def go():
        await c.receive_json({"type": "send", "message": "q"})
        await c.task
    async_to_sync(go)()
    c.send_json.assert_awaited_with({"type": "delta", "text": "mine"})


def test_a_user_whose_scope_permission_is_revoked_stops_receiving_group_frames(chat, editor, settings_shared):
    c = ChatConsumer(); c.scope = {"user": editor, "session": None}; c.send_json = AsyncMock(); c.close = AsyncMock()
    c._session_valid = AsyncMock(return_value=True); c.get_object = AsyncMock(return_value=chat); c._last_group_access = 0
    async_to_sync(c.chat_frame)({"frame": {"type": "delta", "text": "visible"}})
    c.send_json.assert_awaited_once()
    settings_shared["allowed"] = False  # the app revokes the editor's scope access mid-turn
    c.send_json.reset_mock(); c._last_group_access = 0
    async_to_sync(c.chat_frame)({"frame": {"type": "delta", "text": "secret"}})
    c.send_json.assert_not_awaited()
    c.close.assert_awaited_with(code=4403)


def test_shared_turn_reserves_spend_against_the_sender_not_the_creator(chat, owner, editor, monkeypatch):
    from django_core_micha.assistant.drivers import openai_api
    reserved = []
    class Responses:
        async def create(self, **kwargs):
            async def stream():
                yield NS(type="response.output_text.delta", delta="ok")
                yield NS(type="response.completed", response=NS(usage=NS(input_tokens=1, output_tokens=1), output=[]))
            return stream()
    monkeypatch.setattr(openai_api, "_reserve", lambda uid, *a: reserved.append(uid) or object())
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kw: None)
    monkeypatch.setattr(openai_api, "release_reservation", lambda **kw: None)
    monkeypatch.setattr(turns, "resolve_credential", lambda u, d: ("key-of-" + u.username, "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda c: "sys")
    monkeypatch.setattr(turns, "tools_for_chat", lambda c: [])
    monkeypatch.setattr(turns, "_make_driver", lambda name, credential: openai_api.OpenAIDriver(NS(responses=Responses())))
    async def go(): return await turns.run_turn(chat, editor, "question", AsyncMock())
    final = async_to_sync(go)()
    assert reserved == [editor.pk] and owner.pk not in reserved
    assert final.sender_id == editor.pk and AssistantTurn.objects.get(pk=final.pk).sender_id == editor.pk


def test_context_rejection_never_reaches_run_turn(chat, owner, monkeypatch):
    called = Mock()
    monkeypatch.setattr(consumers, "run_turn", called)
    monkeypatch.setattr(consumers, "validate_client_context", lambda *a: (_ for _ in ()).throw(ValueError("no")))
    c = ChatConsumer(); c.chat = chat; c.scope = {"user": owner}; c.task = None; c.send_json = AsyncMock()
    async_to_sync(c.receive_json)({"type": "send", "message": "q", "context": {"x": 1}})
    assert c.send_json.await_args.args[0]["code"] == "invalid_frame" and called.call_count == 0 and c.task is None
    # the size bound alone, with a hook that would accept everything
    monkeypatch.setattr(consumers, "validate_client_context", lambda ch, u, ctx: ctx)
    with override_settings(DCM_ASSISTANT_LIMITS={"max_client_context_bytes": 8}):
        c.send_json.reset_mock()
        async_to_sync(c.receive_json)({"type": "send", "message": "q", "context": {"a": "much longer than eight bytes"}})
    assert c.send_json.await_args.args[0]["code"] == "invalid_frame" and called.call_count == 0


@pytest.mark.parametrize("fail", [False, True])
def test_completion_removes_the_cli_session_directory_the_driver_created(monkeypatch, tmp_path, owner, fail):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.claude_cli import _session_dirs
    rows = [{"driver": "claude_cli", "model": "c", "efforts": ["low"], "billing": "subscription", "default": True}]
    created = []
    class Driver:
        async def stream(self, **kw):
            root, config, sessions = _session_dirs(kw["chat"].pk, True)  # what the real CLI driver does
            created.append(root)
            (root / "session.jsonl").write_text("{}", encoding="utf-8")
            if fail: raise DriverError("provider_error")
            yield {"type": "delta", "text": "ok"}
    monkeypatch.setattr(completion, "resolve_credential", lambda u, d: ("tok", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *a: Driver())
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path, DCM_ASSISTANT_CATALOGUE=rows):
        if fail:
            with pytest.raises(DriverError): async_to_sync(completion.acomplete)(owner, prompt="p", driver="claude_cli", model="c")
        else:
            assert async_to_sync(completion.acomplete)(owner, prompt="p", driver="claude_cli", model="c").text == "ok"
    assert created and not created[0].exists()


def test_a_broken_shared_scope_hook_fails_closed():
    from django_core_micha.assistant.hooks import is_shared_scope
    with override_settings(DCM_ASSISTANT_SHARED_SCOPE="no.such.module.hook"):
        assert is_shared_scope("shared") is False


def test_creator_without_scope_permission_cannot_start_a_turn(chat, owner, settings_shared):
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: False):
        with pytest.raises(turns.AccessLost):
            turns._create_turn(chat.pk, owner.pk, "hello")
    assert not AssistantTurn.objects.filter(chat=chat).exists()


def test_a_hook_with_an_explicit_client_context_parameter_receives_it():
    from django_core_micha.assistant.hooks import build_turn_context
    seen = {}
    def hook(chat, user, message, turn, client_context=None):
        seen["ctx"] = client_context
        return "text"
    def legacy(chat, user, message, turn):
        return "legacy"
    with override_settings(DCM_ASSISTANT_TURN_CONTEXT=hook):
        assert build_turn_context(None, None, "m", None, client_context={"draft": 1}) == "text"
    assert seen["ctx"] == {"draft": 1}
    with override_settings(DCM_ASSISTANT_TURN_CONTEXT=legacy):
        assert build_turn_context(None, None, "m", None, client_context={"draft": 1}) == "legacy"


def test_second_user_cannot_start_a_turn_while_another_runs(chat, owner, editor):
    AssistantTurn.objects.create(chat=chat, sequence=1, user_text="running", sender=owner, driver="openai_api", model="m",
                                 effort="low", heartbeat_at=__import__("django.utils.timezone", fromlist=["now"]).now())
    with pytest.raises(DriverError) as error:
        turns._create_turn(chat.pk, editor.pk, "second")
    assert error.value.code == "turn_active"


def test_completion_removes_the_cli_session_directory_after_a_timeout(monkeypatch, tmp_path, owner):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.claude_cli import _session_dirs
    rows = [{"driver": "claude_cli", "model": "c", "efforts": ["low"], "billing": "subscription", "default": True}]
    created = []
    class Slow:
        async def stream(self, **kw):
            root, config, sessions = _session_dirs(kw["chat"].pk, True)
            created.append(root)
            await asyncio.sleep(5)
            yield {"type": "delta", "text": "late"}
    monkeypatch.setattr(completion, "resolve_credential", lambda u, d: ("tok", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *a: Slow())
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path, DCM_ASSISTANT_CATALOGUE=rows, DCM_ASSISTANT_LIMITS={"provider_timeout": 1}):
        with pytest.raises(DriverError) as error:
            async_to_sync(completion.acomplete)(owner, prompt="p", driver="claude_cli", model="c")
    assert error.value.code == "timeout" and created and not created[0].exists()


def test_unscoped_list_shows_only_own_chats_even_with_a_shared_chat_of_another_user(chat, owner, editor):
    from rest_framework.test import APIRequestFactory, force_authenticate
    from django_core_micha.assistant.views import ChatListCreateView
    mine = AssistantChat.objects.create(owner=editor, scope="", selected_driver="openai_api", selected_model="m")
    request = APIRequestFactory().get("/"); force_authenticate(request, user=editor)
    ids = [row["id"] for row in ChatListCreateView.as_view()(request).data["results"]]
    assert ids == [str(mine.pk)]


def _patch_turns(monkeypatch, driver):
    monkeypatch.setattr(turns, "resolve_credential", lambda u, d: ("key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda c: "sys")
    monkeypatch.setattr(turns, "tools_for_chat", lambda c: [])
    monkeypatch.setattr(turns, "_make_driver", lambda *a: driver)


def test_unsharing_a_scope_during_a_turn_cancels_a_non_creator_sender(chat, editor, monkeypatch):
    class Hanging:
        async def stream(self, **kw):
            yield {"type": "init"}
            await asyncio.Event().wait()
    _patch_turns(monkeypatch, Hanging())
    calls = {"n": 0}
    def shared(scope):
        calls["n"] += 1
        return calls["n"] < 3  # shared when the turn starts, owner-only at the first re-check
    async def go():
        with pytest.raises(turns.AccessLost):
            await turns.run_turn(chat, editor, "hello", AsyncMock())
    with override_settings(DCM_ASSISTANT_SHARED_SCOPE=shared, DCM_ASSISTANT_LIMITS={"access_recheck_seconds": 1}):
        async_to_sync(go)()
    assert AssistantTurn.objects.get(chat=chat).state == "cancelled"


def test_oversized_repair_feedback_fails_the_turn_without_a_second_provider_call(chat, owner, monkeypatch):
    from django_core_micha.assistant.hooks import AfterTurnResult
    calls = []
    class Driver:
        async def stream(self, **kw):
            calls.append(1)
            yield {"type": "delta", "text": "draft"}
    _patch_turns(monkeypatch, Driver())
    async def go():
        with pytest.raises(DriverError) as error:
            await turns.run_turn(chat, owner, "hello", AsyncMock())
        return error.value.code
    with override_settings(DCM_ASSISTANT_AFTER_TURN=lambda c, u, t, text: AfterTurnResult(repair="x" * 500),
                           DCM_ASSISTANT_LIMITS={"max_prompt_bytes": 400}):
        code = async_to_sync(go)()
    assert code == "prompt_too_long" and len(calls) == 1
    assert AssistantTurn.objects.get(chat=chat).state == "failed"


def test_a_failing_session_recheck_withholds_the_group_frame_and_closes(chat, editor):
    c = ChatConsumer(); c.scope = {"user": editor, "session": None}; c.send_json = AsyncMock(); c.close = AsyncMock()
    c._session_valid = AsyncMock(side_effect=RuntimeError("store down")); c.get_object = AsyncMock(return_value=chat); c._last_group_access = 0
    async_to_sync(c.chat_frame)({"frame": {"type": "delta", "text": "secret"}})
    c.send_json.assert_not_awaited()
    c.close.assert_awaited_with(code=4403)


def test_repair_follow_up_that_pushes_the_history_over_the_prompt_bound_fails_the_turn(chat, owner, monkeypatch):
    from django_core_micha.assistant.hooks import AfterTurnResult
    calls = []
    class Driver:
        async def stream(self, **kw):
            calls.append(1)
            yield {"type": "delta", "text": "d" * 600}
    _patch_turns(monkeypatch, Driver())
    async def go():
        with pytest.raises(DriverError) as error:
            await turns.run_turn(chat, owner, "hello", AsyncMock())
        return error.value.code
    # the first prompt fits; the follow-up (history + 600-byte answer + feedback) does not
    with override_settings(DCM_ASSISTANT_AFTER_TURN=lambda c, u, t, text: AfterTurnResult(repair="fix it"),
                           DCM_ASSISTANT_LIMITS={"max_prompt_bytes": 500}):
        code = async_to_sync(go)()
    assert code == "prompt_too_long" and len(calls) == 1


def test_completion_rejects_a_prompt_over_the_size_bound_before_any_provider_call(owner, monkeypatch):
    from django_core_micha.assistant import completion
    calls = []
    class Driver:
        async def stream(self, **kw):
            calls.append(1)
            yield {"type": "delta", "text": "never"}
    monkeypatch.setattr(completion, "resolve_credential", lambda u, d: ("key", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *a: Driver())
    with override_settings(DCM_ASSISTANT_LIMITS={"max_prompt_bytes": 100}):
        with pytest.raises(DriverError) as error:
            async_to_sync(completion.acomplete)(owner, prompt="x" * 200, driver="openai_api", model="m")
    assert error.value.code == "prompt_too_long" and calls == []
