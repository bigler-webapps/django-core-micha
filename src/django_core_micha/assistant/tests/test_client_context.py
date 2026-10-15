import asyncio
from unittest.mock import AsyncMock, Mock
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
pytestmark=pytest.mark.django_db
@pytest.fixture(autouse=True)
def catalog():
 with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s: True, DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"m","efforts":["low","medium"],"billing":"pay_per_use","default":True},{"driver":"anthropic_api","model":"a","efforts":["low"],"billing":"pay_per_use"},{"driver":"claude_cli","model":"c","efforts":["low"],"billing":"subscription"}]): yield
@pytest.fixture
def user(): return get_user_model().objects.create_user(username="new-user",password="pw")
@pytest.fixture
def chat(user): return AssistantChat.objects.create(owner=user,scope="s",selected_driver="openai_api",selected_model="m",selected_effort="low")

def setup(monkeypatch, driver):
 from django_core_micha.assistant import turns
 monkeypatch.setattr(turns,"resolve_credential",lambda *a:("key","deployment")); monkeypatch.setattr(turns,"build_system_prompt",lambda c:"sys"); monkeypatch.setattr(turns,"tools_for_chat",lambda c:[]); monkeypatch.setattr(turns,"_make_driver",lambda *a:driver)
def test_context_reaches_new_hook_and_legacy_hook_still_works(user,chat,monkeypatch):
 from django_core_micha.assistant import hooks
 seen=[]
 class D:
  async def stream(self,**kw): yield {"type":"delta","text":"ok"}
 setup(monkeypatch,D()); monkeypatch.setattr(hooks,"_hook",lambda n: ((lambda *a,**kw: seen.append((a,kw)) or None) if n=="TURN_CONTEXT" else ((lambda u,s: True) if n=="SCOPE_PERMISSION" else None)))
 async def run(): await __import__('django_core_micha.assistant.turns',fromlist=['run_turn']).run_turn(chat,user,"q",lambda f:asyncio.sleep(0),client_context={"draft":1})
 async_to_sync(run)(); assert seen[0][1]["client_context"]=={"draft":1}
 seen.clear(); monkeypatch.setattr(hooks,"_hook",lambda n: ((lambda a,b,c,d: seen.append((a,b,c,d)) or None) if n=="TURN_CONTEXT" else ((lambda u,s: True) if n=="SCOPE_PERMISSION" else None)))
 chat.turns.all().delete(); async_to_sync(run)(); assert len(seen[0])==4
def test_context_size_and_hook_rejection_precede_turn(user,chat,monkeypatch):
 from django_core_micha.assistant.consumers import ChatConsumer
 from django_core_micha.assistant import consumers
 monkeypatch.setattr(consumers,"validate_client_context",lambda *a: (_ for _ in ()).throw(ValueError()))
 c=ChatConsumer(); c.chat=chat; c.scope={"user":user}; c.task=None; c.send_json=AsyncMock(); c._session_valid=AsyncMock(return_value=True)
 with override_settings(DCM_ASSISTANT_LIMITS={"max_client_context_bytes":8}): async_to_sync(c.receive_json)({"type":"send","message":"q","context":{"x":1}})
 assert c.send_json.await_args.args[0]["code"]=="invalid_frame" and not AssistantTurn.objects.filter(chat=chat).exists()
 c.send_json.reset_mock(); async_to_sync(c.receive_json)({"type":"send","message":"q","context":{"a":"long long"}})
 assert c.send_json.await_args.args[0]["code"]=="invalid_frame" and not AssistantTurn.objects.filter(chat=chat).exists()

def test_normalized_context_is_passed_to_run_turn(user,chat,monkeypatch):
 from django_core_micha.assistant import consumers
 c=consumers.ChatConsumer(); c.chat=chat; c.scope={"user":user}; c.task=None; c.send_json=AsyncMock()
 monkeypatch.setattr(consumers,"validate_client_context",lambda ch,u,ctx:{"normalized":ctx["draft"]})
 called=AsyncMock(); monkeypatch.setattr(consumers,"run_turn",called)
 async def receive_then_wait():
  await c.receive_json({"type":"send","message":"q","context":{"draft":3}})
  await c.task
 async_to_sync(receive_then_wait)()
 assert called.await_args.kwargs["client_context"]=={"normalized":3}
