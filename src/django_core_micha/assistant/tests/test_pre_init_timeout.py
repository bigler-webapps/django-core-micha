import asyncio

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def catalogue_settings():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver": "claude_cli", "model": "claude-test", "name": "CLI",
            "efforts": ["low", "medium", "high", "max"], "billing": "subscription", "default": True}]):
        yield


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="timeout-user", password="secret")


@pytest.fixture
def chat(user):
    return AssistantChat.objects.create(owner=user, selected_driver="claude_cli", selected_model="claude-test",
        selected_effort="medium")


def _run_turn(monkeypatch, driver, chat, user, frames):
    from django_core_micha.assistant import turns

    monkeypatch.setattr(turns, "resolve_credential", lambda current_user, name: ("credential", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda current_chat: "system")
    monkeypatch.setattr(turns, "tools_for_chat", lambda chat: [])
    monkeypatch.setattr(turns, "_make_driver", lambda *args: driver)

    async def emit(frame):
        frames.append(frame)

    async def run():
        return await turns.run_turn(chat, user, "question", emit)

    return run


class HangingDriver:
    def __init__(self, init=False):
        self.init = init

    async def stream(self, **kwargs):
        if self.init:
            yield {"type": "init"}
        await asyncio.Event().wait()


def _prepare_resumed_session(chat, tmp_path):
    chat.session_driver, chat.session_reference = "claude_cli", "old-session"
    chat.save(update_fields=("session_driver", "session_reference"))
    project = tmp_path / str(chat.pk) / "config" / "projects" / "project-a"
    project.mkdir(parents=True)
    (project / "old-session.jsonl").write_text("{}\n", encoding="utf-8")


def test_turn_timeout_before_init_discards_resumed_session(user, chat, monkeypatch, tmp_path):
    _prepare_resumed_session(chat, tmp_path)
    frames = []
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path, DCM_ASSISTANT_LIMITS={"provider_timeout": 1}):
        with pytest.raises(DriverError) as error:
            async_to_sync(_run_turn(monkeypatch, HangingDriver(), chat, user, frames))()
    chat.refresh_from_db()
    turn = AssistantTurn.objects.get(chat=chat)
    assert error.value.code == "timeout"
    assert turn.state == "failed" and turn.error_code == "timeout"
    assert chat.session_driver == "" and chat.session_reference == ""


def test_turn_timeout_after_init_keeps_resumed_session(user, chat, monkeypatch, tmp_path):
    _prepare_resumed_session(chat, tmp_path)
    frames = []
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path, DCM_ASSISTANT_LIMITS={"provider_timeout": 1}):
        with pytest.raises(DriverError) as error:
            async_to_sync(_run_turn(monkeypatch, HangingDriver(init=True), chat, user, frames))()
    chat.refresh_from_db()
    turn = AssistantTurn.objects.get(chat=chat)
    assert error.value.code == "timeout"
    assert turn.state == "failed" and turn.error_code == "timeout"
    assert chat.session_driver == "claude_cli" and chat.session_reference == "old-session"


def test_fresh_turn_timeout_before_init_leaves_session_fields_unchanged(user, chat, monkeypatch, tmp_path):
    chat.session_driver, chat.session_reference = "claude_cli", "missing-session"
    chat.save(update_fields=("session_driver", "session_reference"))
    frames = []
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path, DCM_ASSISTANT_LIMITS={"provider_timeout": 1}):
        with pytest.raises(DriverError) as error:
            async_to_sync(_run_turn(monkeypatch, HangingDriver(), chat, user, frames))()
    chat.refresh_from_db()
    turn = AssistantTurn.objects.get(chat=chat)
    assert error.value.code == "timeout"
    assert turn.state == "failed" and turn.error_code == "timeout"
    assert chat.session_driver == "claude_cli" and chat.session_reference == "missing-session"
