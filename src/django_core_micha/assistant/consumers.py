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

from .hooks import can_access_scope
from .limits import get_limit
from .models import AssistantChat
from .turns import reconcile_interrupted, run_turn, turn_payload

class IsChatOwnerWs(WsPermission):
    async def has_permission(self, scope, consumer):
        try:
            chat=await consumer.get_object()
            return bool(chat and chat.owner_id==scope["user"].pk and await sync_to_async(can_access_scope)(scope["user"],chat.scope))
        except (ObjectDoesNotExist,Http404,ValueError,TypeError): return False

class ChatConsumer(BaseSecureConsumer, AsyncJsonWebsocketConsumer):
    permission_classes_ws=[IsAuthenticatedWs,IsChatOwnerWs]
    async def get_object(self):
        return await sync_to_async(AssistantChat.objects.filter(owner=self.scope["user"],pk=self.scope["url_route"]["kwargs"]["chat_id"]).first)()
    async def post_connect(self):
        self.chat=await self.get_object()
        if not self.chat: await self.close(code=4403); return
        self.frames=deque(); self.task=None
        await sync_to_async(reconcile_interrupted)(self.chat)
        rows=await sync_to_async(list)(self.chat.turns.order_by("sequence"))
        await self.send_json({"type":"history","chat_id":str(self.chat.pk),"turns":[turn_payload(t) for t in rows]})
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
        if bytes_data is not None or (text_data and len(text_data.encode("utf-8"))>get_limit("max_message_length")*4):
            await self.send_json({"type":"error","code":"invalid_frame"}); return
        if not await self._session_valid(): await self.close(code=4401); return
        chat=await self.get_object()
        if chat is None or not await sync_to_async(can_access_scope)(self.scope["user"],chat.scope): await self.close(code=4403); return
        self.chat=chat
        await super().receive(text_data=text_data,bytes_data=bytes_data,**kwargs)
    async def receive_json(self,content,**kwargs):
        if not isinstance(content,dict): await self.send_json({"type":"error","code":"invalid_frame"}); return
        if content=={"type":"stop"}:
            if self.task and not self.task.done(): self.task.cancel()
            return
        if set(content)!={"type","message"} or content.get("type")!="send" or not isinstance(content.get("message"),str):
            await self.send_json({"type":"error","code":"invalid_frame"}); return
        if self.chat.archived_at: await self.send_json({"type":"error","code":"archived"}); return
        if self.task and not self.task.done(): await self.send_json({"type":"error","code":"turn_active"}); return
        async def execute():
            try: await run_turn(self.chat,self.scope["user"],content["message"],self.send_json)
            except Exception as exc: await self.send_json({"type":"error","code":getattr(exc,"code","provider_error")})
        self.task=asyncio.create_task(execute())
    async def disconnect(self,code):
        if getattr(self,"task",None) and not self.task.done(): self.task.cancel()
