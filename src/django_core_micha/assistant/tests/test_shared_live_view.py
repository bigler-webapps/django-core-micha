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
 with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"m","efforts":["low","high"],"billing":"subscription","default":True}],DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s: s!="denied",DCM_ASSISTANT_SHARED_SCOPE=lambda s:s=="shared"): yield
@pytest.fixture
def users(): return [get_user_model().objects.create_user(username=f"shared-{i}",password="pw") for i in range(3)]
def req(view,method,user,*args,data=None,query=None):
 f=APIRequestFactory(); r=getattr(f,method)("/",data=data,format="json",**({"data":query} if query is not None and method=="get" else {})); force_authenticate(r,user=user); return view.as_view()(r,*args)

def test_shared_group_frame_rechecks_and_closes_on_revocation(users):
 owner,editor,_=users; chat=AssistantChat.objects.create(owner=owner,scope="shared",selected_driver="openai_api",selected_model="m")
 from django_core_micha.assistant.consumers import ChatConsumer
 c=ChatConsumer(); c.scope={"user":editor,"url_route":{"kwargs":{"chat_id":str(chat.pk)}},"session":None}; c.shared_group=True; c.group_name="g"; c.channel_layer=None; c.channel_name="x"; c._last_group_access=0
 c.send_json=AsyncMock(); c.close=AsyncMock(); c._session_valid=AsyncMock(return_value=True); c.get_object=AsyncMock(return_value=chat)
 second=ChatConsumer(); second.scope={"user":owner,"url_route":{"kwargs":{"chat_id":str(chat.pk)}},"session":None}; second.shared_group=True; second.group_name="g"; second.channel_layer=None; second.channel_name="y"; second._last_group_access=0; second.send_json=AsyncMock(); second.close=AsyncMock(); second._session_valid=AsyncMock(return_value=True); second.get_object=AsyncMock(return_value=chat)
 frame={"frame":{"type":"delta","text":"x"}}; async_to_sync(c.chat_frame)(frame); async_to_sync(second.chat_frame)(frame); c.send_json.assert_awaited_once(); second.send_json.assert_awaited_once()
 c.send_json.reset_mock(); c._last_group_access=0; c.get_object=AsyncMock(return_value=None)
 async_to_sync(c.chat_frame)({"frame":{"type":"delta","text":"secret"}}); c.send_json.assert_not_awaited(); c.close.assert_awaited_with(code=4403)
def test_second_sender_gets_turn_active_and_websocket_inventory():
 from django_core_micha.assistant.consumers import ChatConsumer
 from django_core_micha.assistant.turns import _create_turn
 from django_core_micha.assistant.models import AssistantChat
 from django_core_micha.assistant import consumers
 try:
  from django_core_micha.auth.security import assert_all_consumers_secure
 except ImportError:
  from django_core_micha.auth.ws_permissions import assert_all_consumers_secure
 assert assert_all_consumers_secure(["django_core_micha.assistant.consumers"])==[]

def test_second_send_while_turn_active_gets_error(users):
 owner,editor,_=users; chat=AssistantChat.objects.create(owner=owner,scope="shared",selected_driver="openai_api",selected_model="m")
 from django_core_micha.assistant.consumers import ChatConsumer
 c=ChatConsumer(); c.chat=chat; c.scope={"user":editor}; c.task=type("Pending",(),{"done":lambda self:False})(); c.send_json=AsyncMock()
 async_to_sync(c.receive_json)({"type":"send","message":"second"}); assert c.send_json.await_args.args[0]=={"type":"error","code":"turn_active"}

def test_socket_permission_denies_owner_only_foreign_and_scope_denied(users):
 from django_core_micha.assistant.consumers import CanAccessChatWs
 owner,editor,denied=users; chat=AssistantChat.objects.create(owner=owner,scope="private")
 class C:
  async def get_object(self): return chat
 permission=CanAccessChatWs()
 assert not async_to_sync(permission.has_permission)({"user":editor},C())
 denied_chat=AssistantChat.objects.create(owner=owner,scope="denied")
 class DeniedC:
  async def get_object(self): return denied_chat
 with override_settings(DCM_ASSISTANT_SHARED_SCOPE=lambda s:True): assert not async_to_sync(permission.has_permission)({"user":denied},DeniedC())
