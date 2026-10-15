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
 with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"m","efforts":["low","medium"],"billing":"pay_per_use","default":True},{"driver":"anthropic_api","model":"a","efforts":["low"],"billing":"pay_per_use"},{"driver":"claude_cli","model":"c","efforts":["low"],"billing":"subscription"}]): yield
@pytest.fixture
def user(): return get_user_model().objects.create_user(username="new-user",password="pw")
@pytest.fixture
def chat(user): return AssistantChat.objects.create(owner=user,scope="s",selected_driver="openai_api",selected_model="m",selected_effort="low")

@pytest.mark.parametrize("driver,model",[("openai_api","m"),("anthropic_api","a"),("claude_cli","c")])
def test_completion_returns_text_usage_and_sender_and_cli_cleanup(user,driver,model,monkeypatch,tmp_path):
 from django_core_micha.assistant import completion
 seen={}; calls=[]
 class D:
  async def stream(self,**kw): seen.update(kw); yield {"type":"delta","text":"ok"}; yield {"type":"usage","usage":{"input_tokens":2,"output_tokens":1}}
 monkeypatch.setattr(completion,"resolve_credential",lambda u,d:(calls.append((u.pk,d)) or ("secret","deployment"))); monkeypatch.setattr(completion,"_make_driver",lambda *a:D())
 with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path): result=async_to_sync(completion.acomplete)(user,prompt="p",driver=driver,model=model)
 assert result.text=="ok" and result.usage["input_tokens"]==2 and calls==[(user.pk,driver)] and seen["user"]==user
 if driver=="claude_cli": assert not (tmp_path/seen["chat"].pk).exists()
def test_completion_timeout_maps_to_driver_error(user,monkeypatch):
 from django_core_micha.assistant import completion
 from django_core_micha.assistant.drivers.base import DriverError
 class Slow:
  async def stream(self,**kw): await asyncio.sleep(.1); yield {"type":"delta","text":"late"}
 monkeypatch.setattr(completion,"resolve_credential",lambda *a:("key","deployment")); monkeypatch.setattr(completion,"_make_driver",lambda *a:Slow())
 monkeypatch.setattr(completion,"get_limit",lambda name:0.01 if name=="provider_timeout" else 10)
 with override_settings(DCM_ASSISTANT_LIMITS={"provider_timeout":1}):
  with pytest.raises(DriverError) as error: async_to_sync(completion.acomplete)(user,prompt="p",driver="openai_api",model="m")
 assert error.value.code=="timeout"
@pytest.mark.parametrize("mode",["error","timeout"])
def test_cli_session_directory_is_removed_after_error_or_timeout(user,monkeypatch,tmp_path,mode):
 from django_core_micha.assistant import completion
 from django_core_micha.assistant.drivers.base import DriverError
 class Failing:
  async def stream(self,**kw):
   if mode=="timeout": await asyncio.sleep(.1)
   else: raise RuntimeError("provider failure")
   yield {"type":"delta","text":"late"}
 monkeypatch.setattr(completion,"resolve_credential",lambda *a:("oauth","deployment")); monkeypatch.setattr(completion,"_make_driver",lambda *a:Failing())
 monkeypatch.setattr(completion,"get_limit",lambda name:0.01 if name=="provider_timeout" else 10)
 with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path,DCM_ASSISTANT_LIMITS={"provider_timeout":1}):
  directory=tmp_path/"completion-test"; directory.mkdir()
  monkeypatch.setattr(completion.uuid,"uuid4",lambda:"test")
  directory=tmp_path/"completion-test"; directory.mkdir(exist_ok=True)
  with pytest.raises(DriverError): async_to_sync(completion.acomplete)(user,prompt="p",driver="claude_cli",model="c")
  assert not (tmp_path/"completion-test").exists() and not (tmp_path/"completion-test").is_dir()

def test_completion_uses_api_cost_guard_for_calling_user(user,monkeypatch):
 from types import SimpleNamespace as NS
 from django_core_micha.assistant import completion
 from django_core_micha.assistant.drivers import openai_api
 from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
 reserved=[]
 class Responses:
  async def create(self,**kwargs):
   async def events():
    yield NS(type="response.output_text.delta",delta="ok")
    yield NS(type="response.completed",response=NS(usage=NS(input_tokens=1,output_tokens=1),output=[]))
   return events()
 monkeypatch.setattr(completion,"resolve_credential",lambda *a:("key","deployment")); monkeypatch.setattr(completion,"_make_driver",lambda *a:OpenAIDriver(NS(responses=Responses())))
 monkeypatch.setattr(openai_api,"_reserve",lambda uid,model,size:reserved.append((uid,model)) or object()); monkeypatch.setattr(openai_api,"settle_reservation",lambda **kw:None); monkeypatch.setattr(openai_api,"release_reservation",lambda **kw:None)
 result=async_to_sync(completion.acomplete)(user,prompt="hello",driver="openai_api",model="m")
 assert result.text=="ok" and reserved==[(user.pk,"m")]
