"""Secure owner-only chat socket."""
import asyncio
import time
from collections import deque
from importlib import import_module

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from django_core_micha.auth.ws_permissions import BaseSecureConsumer, IsAuthenticatedWs, WsPermission

from .hooks import availability_reason, can_access_scope, is_shared_scope, validate_client_context
from .access import can_view_chat
from .limits import get_limit
from .models import AssistantChat
from .history import summary_payload
from .turns import AccessLost, reconcile_interrupted, run_turn, turn_payload

class CanAccessChatWs(WsPermission):
    async def has_permission(self, scope, consumer):
        try:
            chat=await consumer.get_object()
            return bool(chat and await sync_to_async(can_view_chat)(scope["user"],chat))
        except (ObjectDoesNotExist,Http404,ValueError,TypeError): return False

IsChatOwnerWs = CanAccessChatWs

class ChatConsumer(BaseSecureConsumer, AsyncJsonWebsocketConsumer):
    permission_classes_ws=[IsAuthenticatedWs,CanAccessChatWs]
    async def get_object(self):
        return await sync_to_async(AssistantChat.objects.filter(pk=self.scope["url_route"]["kwargs"]["chat_id"]).first)()
    async def post_connect(self):
        self.chat=await self.get_object()
        if not self.chat: await self.close(code=4403); return
        self.frames=deque(); self.task=None
        await sync_to_async(reconcile_interrupted)(self.chat)
        rows=await sync_to_async(list)(self.chat.turns.order_by("sequence"))
        await self.send_json({"type":"history","chat_id":str(self.chat.pk),"turns":[turn_payload(t,self.chat) for t in rows],
            "summary":await sync_to_async(summary_payload)(self.chat)})
        self.group_name = f"dcm_assistant_chat_{self.chat.pk}"
        self.shared_group = bool(self.channel_layer and is_shared_scope(self.chat.scope))
        self._last_group_access = 0.0
        if self.shared_group: await self.channel_layer.group_add(self.group_name,self.channel_name)
    async def _session_valid(self):
        key=getattr(self.scope.get("session"),"session_key",None)
        if not key: return False
        def check():
            store=import_module(settings.SESSION_ENGINE).SessionStore
            user=self.scope["user"]
            return store(key).exists(key) and user.__class__.objects.filter(pk=user.pk,is_active=True).exists()
        return await sync_to_async(check)()
    async def receive(self,text_data=None,bytes_data=None,**kwargs):
        now=time.monotonic(); window=get_limit("frame_rate_window")
        while self.frames and now-self.frames[0]>window: self.frames.popleft()
        self.frames.append(now)
        if len(self.frames)>get_limit("frame_rate_count"): await self.close(code=4429); return
        if bytes_data is not None or (text_data and len(text_data.encode("utf-8"))>get_limit("max_message_length")*4+get_limit("max_client_context_bytes")):
            await self.send_json({"type":"error","code":"invalid_frame"}); return
        if not await self._session_valid(): await self.close(code=4401); return
        chat=await self.get_object()
        if chat is None or not await sync_to_async(can_view_chat)(self.scope["user"],chat): await self.close(code=4403); return
        self.chat=chat
        await super().receive(text_data=text_data,bytes_data=bytes_data,**kwargs)
    async def receive_json(self,content,**kwargs):
        if not isinstance(content,dict): await self.send_json({"type":"error","code":"invalid_frame"}); return
        if content=={"type":"stop"}:
            if self.task and not self.task.done(): self.task.cancel()
            return
        if set(content) not in ({"type","message"},{"type","message","context"}) or content.get("type")!="send" or not isinstance(content.get("message"),str):
            await self.send_json({"type":"error","code":"invalid_frame"}); return
        context = content.get("context")
        if "context" in content:
            if not isinstance(context,dict): await self.send_json({"type":"error","code":"invalid_frame"}); return
            try:
                raw = __import__("json").dumps(context,separators=(",",":"),ensure_ascii=False).encode("utf-8")
                if len(raw)>get_limit("max_client_context_bytes"): raise ValueError()
                context = await sync_to_async(validate_client_context)(self.chat,self.scope["user"],context)
                if not isinstance(context,dict): raise ValueError()
            except Exception:
                await self.send_json({"type":"error","code":"invalid_frame"}); return
        if self.chat.archived_at: await self.send_json({"type":"error","code":"archived"}); return
        if self.task and not self.task.done(): await self.send_json({"type":"error","code":"turn_active"}); return
        reason=await sync_to_async(availability_reason)(self.scope["user"],self.chat.scope)
        if reason: await self.send_json({"type":"error","code":"unavailable","reason":reason}); return
        async def execute():
            try:
                    emit = self.send_json
                    if getattr(self,"shared_group",False):
                        async def emit(frame): await self.channel_layer.group_send(self.group_name,{"type":"chat.frame","frame":frame})
                    await run_turn(self.chat,self.scope["user"],content["message"],emit,authorize=self._session_valid,client_context=context)
            except AccessLost: await self.close(code=4403)
            except Exception as exc: await self.send_json({"type":"error","code":getattr(exc,"code","provider_error")})
        self.task=asyncio.create_task(execute())
    async def chat_frame(self,event):
        now=time.monotonic()
        if now-self._last_group_access>=get_limit("access_recheck_seconds"):
            try:
                chat=await self.get_object()
                allowed=bool(await self._session_valid() and chat and await sync_to_async(can_view_chat)(self.scope["user"],chat))
            except Exception:
                allowed=False  # fail closed: a failed re-check withholds the frame and ends the connection
            if not allowed:
                await self.close(code=4403); return
            self._last_group_access=now
        await self.send_json(event["frame"])
    async def disconnect(self,code):
        if getattr(self,"shared_group",False): await self.channel_layer.group_discard(self.group_name,self.channel_name)
        if getattr(self,"task",None) and not self.task.done(): self.task.cancel()
