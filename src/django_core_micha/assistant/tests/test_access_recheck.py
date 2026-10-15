import asyncio
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.consumers import ChatConsumer
from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
from django_core_micha.assistant.turns import AccessLost

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def catalogue():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"test","name":"test","efforts":["low","medium"],"billing":"pay_per_use","default":True}]): yield

@pytest.fixture
def user(): return get_user_model().objects.create_user(username="access-user", password="secret")

@pytest.fixture
def chat(user): return AssistantChat.objects.create(owner=user, scope="research", selected_driver="openai_api", selected_model="test")

def _setup(monkeypatch, driver):
    from django_core_micha.assistant import turns
    monkeypatch.setattr(turns,"resolve_credential",lambda *_:("key","deployment"))
    monkeypatch.setattr(turns,"build_system_prompt",lambda *_:"system")
    monkeypatch.setattr(turns,"tool_registry",lambda:[])
    monkeypatch.setattr(turns,"_make_driver",lambda *_:driver)

def test_turn_validity_loss_cancels_and_closes_socket_4403(monkeypatch, chat, user):
    class Hanging:
        async def stream(self, **kwargs):
            yield {"type":"init"}
            await asyncio.Event().wait()
    _setup(monkeypatch,Hanging())
    checks=0
    def valid_turn(c,u,t):
        nonlocal checks
        checks += 1
        return checks < 2
    consumer=ChatConsumer(); consumer.chat=chat; consumer.scope={"user":user}; consumer.task=None
    consumer._session_valid=AsyncMock(return_value=True); consumer.close=AsyncMock(); consumer.send_json=AsyncMock()
    async def execute():
        from django_core_micha.assistant.turns import run_turn
        try: await run_turn(chat,user,"hello",consumer.send_json,authorize=consumer._session_valid)
        except AccessLost: await consumer.close(code=4403)
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s:True,DCM_ASSISTANT_TURN_VALIDITY=valid_turn,DCM_ASSISTANT_LIMITS={"access_recheck_seconds":1}):
        async_to_sync(execute)()
    assert consumer.close.await_args.kwargs["code"] == 4403
    row=AssistantTurn.objects.get(chat=chat)
    assert row.state == "cancelled"

def test_user_stop_still_cancels_without_access_lost(chat, user, monkeypatch):
    class Hanging:
        async def stream(self, **kwargs):
            yield {"type":"init"}
            await asyncio.Event().wait()
    _setup(monkeypatch,Hanging())
    async def run():
        from django_core_micha.assistant.turns import run_turn
        task=asyncio.create_task(run_turn(chat,user,"hello",AsyncMock()))
        await asyncio.sleep(0.02); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s:True,DCM_ASSISTANT_LIMITS={"access_recheck_seconds":1}): async_to_sync(run)()
    assert AssistantTurn.objects.get(chat=chat).state == "cancelled"


def test_scope_permission_loss_during_a_running_turn_cancels_it(monkeypatch, chat, user):
    class Hanging:
        async def stream(self, **kwargs):
            yield {"type": "init"}
            await asyncio.Event().wait()
    _setup(monkeypatch, Hanging())
    calls = 0
    def permission(u, s):
        nonlocal calls
        calls += 1
        return calls < 2  # allowed before the provider call, revoked at the first re-check
    async def run():
        from django_core_micha.assistant.turns import run_turn
        with pytest.raises(AccessLost): await run_turn(chat, user, "hello", AsyncMock())
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=permission, DCM_ASSISTANT_LIMITS={"access_recheck_seconds": 1}):
        async_to_sync(run)()
    assert AssistantTurn.objects.get(chat=chat).state == "cancelled"


def test_access_revoked_before_the_provider_call_makes_no_provider_call(monkeypatch, chat, user):
    called = []
    class Driver:
        async def stream(self, **kwargs):
            called.append(1)
            yield {"type": "delta", "text": "never"}
    _setup(monkeypatch, Driver())
    async def run():
        from django_core_micha.assistant.turns import run_turn
        with pytest.raises(AccessLost): await run_turn(chat, user, "hello", AsyncMock())
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: False):
        async_to_sync(run)()
    assert called == []
