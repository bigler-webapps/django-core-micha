import asyncio
from unittest.mock import AsyncMock
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
from django_core_micha.assistant.views import ChatListCreateView,ChatDetailView,TurnsView
pytestmark=pytest.mark.django_db
@pytest.fixture(autouse=True)
def settings_shared():
 with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"m","efforts":["low","high"],"billing":"subscription","default":True}],DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s: s!="denied",DCM_ASSISTANT_SHARED_SCOPE=lambda s:s=="shared",OPENAI_API_KEY="sk-test-provider-configured"): yield
@pytest.fixture
def users(): return [get_user_model().objects.create_user(username=f"shared-{i}",password="pw") for i in range(3)]
def req(view,method,user,*args,data=None,query=None,**kwargs):
 f=APIRequestFactory(); r=(f.get("/",query or {}) if method=="get" else getattr(f,method)("/",data=data,format="json")); force_authenticate(r,user=user); return view.as_view()(r,*args,**kwargs)

def test_shared_scope_crud_and_owner_only_and_denied(users):
 owner,editor,denied=users; chat=AssistantChat.objects.create(owner=owner,scope="shared",selected_driver="openai_api",selected_model="m")
 assert len(req(ChatListCreateView,"get",editor,query={"scope":"shared"}).data["results"])==1
 assert req(ChatDetailView,"get",editor,chat_id=chat.pk).status_code==200
 assert req(ChatDetailView,"patch",editor,chat_id=chat.pk,data={"title":"Shared","selection":{"entry":"openai_api:m","effort":"high"}}).status_code==200
 assert req(ChatDetailView,"patch",editor,chat_id=chat.pk,data={"archived":True}).status_code==200
 assert req(ChatDetailView,"patch",editor,chat_id=chat.pk,data={"archived":False}).status_code==200
 row=AssistantTurn.objects.create(chat=chat,sender=editor,sequence=1,user_text="q",assistant_text="a",state="completed")
 assert req(TurnsView,"get",editor,chat_id=chat.pk).data["results"][0]["sender_id"]==editor.pk
 response=req(ChatDetailView,"delete",editor,chat_id=chat.pk); assert response.status_code==403 and response.data["code"]=="forbidden"
 assert req(ChatDetailView,"delete",owner,chat_id=chat.pk).status_code==204
 owned=AssistantChat.objects.create(owner=owner,scope="private")
 from django.http import Http404
 assert req(ChatDetailView,"get",editor,chat_id=owned.pk).status_code==404
 hidden=AssistantChat.objects.create(owner=owner,scope="denied")
 assert req(ChatDetailView,"get",denied,chat_id=hidden.pk).status_code==404
 assert req(ChatListCreateView,"get",editor,query={"scope":""}).data["results"]==[]
def test_empty_scope_never_shared_even_if_hook_true(users):
 with override_settings(DCM_ASSISTANT_SHARED_SCOPE=lambda scope:True):
  from django_core_micha.assistant.hooks import is_shared_scope
  assert not is_shared_scope("")
def test_turn_credential_and_cost_attribution_sender(users,monkeypatch):
 owner,sender,_=users; chat=AssistantChat.objects.create(owner=owner,scope="shared",selected_driver="openai_api",selected_model="m",selected_effort="low")
 from django_core_micha.assistant import turns
 from django_core_micha.assistant import drivers
 seen=[]
 class D:
  async def stream(self,**kw): seen.append(kw["user"].pk); yield {"type":"delta","text":"ok"}
 monkeypatch.setattr(turns,"resolve_credential",lambda u,d:(seen.append(u.pk) or "key","deployment")); monkeypatch.setattr(turns,"build_system_prompt",lambda c:"sys"); monkeypatch.setattr(turns,"tools_for_chat",lambda c:[]); monkeypatch.setattr(turns,"_make_driver",lambda *a:D())
 row=async_to_sync(turns.run_turn)(chat,sender,"q",lambda f:asyncio.sleep(0)); row.refresh_from_db(); assert row.sender_id==sender.pk and seen==[sender.pk,sender.pk]
