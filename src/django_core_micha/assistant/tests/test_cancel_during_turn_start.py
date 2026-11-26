import asyncio
import threading

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant import turns
from django_core_micha.assistant.models import AssistantChat, AssistantTurn


@pytest.mark.django_db
def test_stop_while_turn_is_starting_finalizes_created_row(monkeypatch):
    user = get_user_model().objects.create_user(username="starting-user")
    chat = AssistantChat.objects.create(owner=user, scope="research", selected_driver="openai_api", selected_model="test")
    original_start = turns._start
    release = threading.Event()

    async def run():
        created = asyncio.Event()
        loop = asyncio.get_running_loop()

        def delayed_start(*args):
            turn = original_start(*args)
            loop.call_soon_threadsafe(created.set)
            if not release.wait(5):
                raise RuntimeError("turn start was not released")
            return turn

        monkeypatch.setattr(turns, "_start", delayed_start)
        task = asyncio.create_task(turns.run_turn(chat, user, "hello", lambda event: None))
        created_wait = asyncio.create_task(created.wait())
        try:
            done, _ = await asyncio.wait({created_wait, task}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task  # Surface a start failure instead of masking it as a timeout.
                pytest.fail("turn finished before its start was paused")
            assert created_wait in done, "turn start did not create a row"
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            created_wait.cancel()

    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda user, scope: True, DCM_ASSISTANT_CATALOGUE=[{"driver": "openai_api", "model": "test", "name": "test", "efforts": ["low", "medium"], "billing": "pay_per_use", "default": True}]):
        async_to_sync(run)()
    turn = AssistantTurn.objects.get(chat=chat)
    assert turn.state == "cancelled"
    assert turn.error_code == "cancelled"
    assert not turn.provider_started
