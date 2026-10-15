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

def configure(monkeypatch,driver):
 from django_core_micha.assistant import turns
 monkeypatch.setattr(turns,"resolve_credential",lambda *a:("key","deployment")); monkeypatch.setattr(turns,"build_system_prompt",lambda c:"sys"); monkeypatch.setattr(turns,"tools_for_chat",lambda c:[]); monkeypatch.setattr(turns,"_make_driver",lambda *a:driver)
def test_structured_suppresses_deltas_and_repairs_and_stores_accepted(user,chat,monkeypatch):
 from django_core_micha.assistant.hooks import AfterTurnResult
 class D:
  calls=[]
  async def stream(self,**kw):
   self.calls.append(kw); yield {"type":"delta","text":f"v{len(self.calls)}"}; yield {"type":"usage","usage":{"input_tokens":1,"output_tokens":1}}
 d=D(); configure(monkeypatch,d); n=[]
 from django_core_micha.assistant import turns
 monkeypatch.setattr(turns,"process_after_turn",lambda c,u,t,text: (n.append(text) or (AfterTurnResult(repair="fix") if len(n)==1 else AfterTurnResult(text="accepted"))))
 frames=[]
 async def run(): return await turns.run_turn(chat,user,"q",lambda f: put(frames,f))
 async def put(fs,f): fs.append(f)
 with override_settings(DCM_ASSISTANT_STRUCTURED_TURNS=True): row=async_to_sync(run)()
 assert len(d.calls)==2 and not any(f["type"]=="delta" for f in frames) and frames[-1]["type"]=="turn"
 row.refresh_from_db(); assert row.assistant_text=="accepted" and row.usage["input_tokens"]==2
def test_repair_exhaustion_fallback_and_each_round(user,chat,monkeypatch):
 from django_core_micha.assistant import turns
 from django_core_micha.assistant.hooks import AfterTurnResult
 class D:
  count=0
  async def stream(self,**kw): self.count+=1; yield {"type":"delta","text":"raw"}
 d=D(); configure(monkeypatch,d); monkeypatch.setattr(turns,"process_after_turn",lambda *a:AfterTurnResult(text="fallback",repair="again")); frames=[]
 async def collect(f): frames.append(f)
 with override_settings(DCM_ASSISTANT_LIMITS={"max_repair_rounds":2}): row=async_to_sync(turns.run_turn)(chat,user,"q",collect)
 row.refresh_from_db(); assert d.count==3 and row.assistant_text=="fallback"

def test_api_repair_round_reserves_cost_each_call_and_sends_feedback(user,chat,monkeypatch):
 from types import SimpleNamespace as NS
 from django_core_micha.assistant import turns
 from django_core_micha.assistant.hooks import AfterTurnResult
 from django_core_micha.assistant.drivers import openai_api
 reservations=[]; requests=[]
 class Responses:
  async def create(self,**kw):
   requests.append(kw)
   async def events():
    yield NS(type="response.output_text.delta",delta="draft" if len(requests)==1 else "valid")
    yield NS(type="response.completed",response=NS(usage=NS(input_tokens=2,output_tokens=1),output=[]))
   return events()
 class Client: responses=Responses()
 monkeypatch.setattr(openai_api,"_reserve",lambda user_id,model,size: reservations.append(user_id) or object())
 monkeypatch.setattr(openai_api,"settle_reservation",lambda **kw:None); monkeypatch.setattr(openai_api,"release_reservation",lambda **kw:None)
 from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
 monkeypatch.setattr(turns,"resolve_credential",lambda u,d:("key","deployment")); monkeypatch.setattr(turns,"build_system_prompt",lambda c:"system"); monkeypatch.setattr(turns,"tools_for_chat",lambda c:[]); monkeypatch.setattr(turns,"_make_driver",lambda *a:OpenAIDriver(Client()))
 attempts=[]; monkeypatch.setattr(turns,"process_after_turn",lambda c,u,t,text:(attempts.append(text) or (AfterTurnResult(repair="Return valid JSON") if len(attempts)==1 else None)))
 async def collect(_): pass
 row=async_to_sync(turns.run_turn)(chat,user,"q",collect); row.refresh_from_db()
 assert len(reservations)==2 and reservations==[user.pk,user.pk]
 assert requests[1]["input"][-2:]==[{"role":"assistant","content":"draft"},{"role":"user","content":"Return valid JSON"}]
 assert row.assistant_text=="valid"
