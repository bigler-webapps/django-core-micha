import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.drivers.claude_cli import ClaudeCliDriver
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def catalogue_settings():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver": "claude_cli", "model": "claude-test", "name": "CLI",
            "efforts": ["low", "medium", "high", "max"], "billing": "subscription", "default": True}]):
        yield


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="cli-user", password="secret")


@pytest.fixture
def chat(user):
    return AssistantChat.objects.create(owner=user, selected_driver="claude_cli", selected_model="claude-test",
        selected_effort="medium")


def _cli_proc(rows):
    class Input:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    class Output:
        def __init__(self):
            self.lines = iter(rows)

        async def readline(self):
            return next(self.lines, b"")

    class Proc:
        pid = 1
        returncode = None

        def __init__(self):
            self.stdin = Input()
            self.stdout = Output()
            self.stderr = NS(read=AsyncMock(return_value=b""))

        async def wait(self):
            self.returncode = 0
            return 0

    return Proc()


def _init(tools=None):
    return {"type": "system", "subtype": "init", "tools": tools or [], "mcp_servers": []}


def _driver(monkeypatch, rows):
    import django_core_micha.assistant.drivers.claude_cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: "claude")
    proc = _cli_proc([((json.dumps(row) + "\n").encode("utf-8")) for row in rows])

    async def factory(*args, **kwargs):
        return proc

    return ClaudeCliDriver(process_factory=factory, kill_tree=lambda proc: None)


def test_pre_init_system_event_is_ignored_and_turn_completes(monkeypatch, tmp_path):
    driver = _driver(monkeypatch, [
        {"type": "system", "subtype": "ui_invalidate"},
        _init(),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}},
        {"type": "result", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ])

    async def run():
        return [event async for event in driver.stream(prompt="p", model="m", tools=[], credential="x", chat=NS(pk="preinit"))]

    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        events = async_to_sync(run)()
    assert [event["text"] for event in events if event["type"] == "delta"] == ["hello"]
    assert all(event.get("subtype") != "ui_invalidate" for event in events)


@pytest.mark.parametrize("rows", [
    [{"type": "assistant", "content": "early"}, _init()],
    [{"type": "system", "subtype": "ui_invalidate"}, _init(["unexpected-tool"])],
])
def test_non_system_before_init_and_wrong_init_still_fail_closed(monkeypatch, tmp_path, rows):
    driver = _driver(monkeypatch, rows)

    async def run():
        return [event async for event in driver.stream(prompt="p", model="m", tools=[], credential="x", chat=NS(pk="bad-init"))]

    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as error:
            async_to_sync(run)()
    assert error.value.code == "security_violation"
    assert error.value.before_init is True


def _run_turn(turns, monkeypatch, driver, chat, user, frames):
    monkeypatch.setattr(turns, "resolve_credential", lambda current_user, name: ("credential", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda current_chat: "system")
    monkeypatch.setattr(turns, "tool_registry", lambda: [])
    monkeypatch.setattr(turns, "_make_driver", lambda *args: driver)

    async def emit(frame):
        frames.append(frame)

    async def run():
        return await turns.run_turn(chat, user, "question", emit)

    return run


def test_resumed_pre_init_failure_discards_reference_and_next_turn_rebuilds_history(user, chat, monkeypatch, tmp_path):
    from django_core_micha.assistant import turns

    AssistantTurn.objects.create(chat=chat, sequence=1, user_text="earlier", assistant_text="saved reply", state="completed")
    chat.session_driver, chat.session_reference = "claude_cli", "old-session"
    chat.save(update_fields=("session_driver", "session_reference"))
    project = tmp_path / str(chat.pk) / "config" / "projects" / "project-a"
    project.mkdir(parents=True)
    (project / "old-session.jsonl").write_text("{}\n", encoding="utf-8")
    seen = []

    class Driver:
        async def stream(self, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                error = DriverError("provider_error")
                error.before_init = True
                raise error
            yield {"type": "delta", "text": "recovered"}
            yield {"type": "session", "session_id": "fresh-session"}

    driver = Driver()
    frames = []
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as error:
            async_to_sync(_run_turn(turns, monkeypatch, driver, chat, user, frames))()
        assert error.value.code == "provider_error"
        chat.refresh_from_db()
        assert chat.session_driver == "" and chat.session_reference == ""
        async_to_sync(_run_turn(turns, monkeypatch, driver, chat, user, frames))()

    assert seen[0]["session_id"] == "old-session"
    assert seen[1]["session_id"] is None
    assert seen[1]["messages"][:2] == [
        {"role": "user", "content": "earlier"}, {"role": "assistant", "content": "saved reply"}
    ]


def test_fresh_pre_init_failure_does_not_discard_chat_session_fields(user, chat, monkeypatch):
    from django_core_micha.assistant import turns

    chat.session_driver, chat.session_reference = "claude_cli", "missing-session"
    chat.save(update_fields=("session_driver", "session_reference"))

    class Driver:
        async def stream(self, **kwargs):
            error = DriverError("provider_error")
            error.before_init = True
            raise error
            yield

    with pytest.raises(DriverError):
        async_to_sync(_run_turn(turns, monkeypatch, Driver(), chat, user, []))()
    chat.refresh_from_db()
    assert chat.session_driver == "claude_cli" and chat.session_reference == "missing-session"


def test_stop_before_init_on_a_resumed_session_discards_the_reference(user, chat, monkeypatch, tmp_path):
    from django_core_micha.assistant import turns

    chat.session_driver, chat.session_reference = "claude_cli", "old-session"
    chat.save(update_fields=("session_driver", "session_reference"))
    project = tmp_path / str(chat.pk) / "config" / "projects" / "project-a"
    project.mkdir(parents=True)
    (project / "old-session.jsonl").write_text("{}\n", encoding="utf-8")

    class Driver:
        async def stream(self, **kwargs):
            error = asyncio.CancelledError()
            error.before_init = True
            raise error
            yield {}

    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(asyncio.CancelledError):
            async_to_sync(_run_turn(turns, monkeypatch, Driver(), chat, user, []))()
    chat.refresh_from_db()
    assert chat.session_driver == "" and chat.session_reference == ""
