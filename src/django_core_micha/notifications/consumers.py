from channels.generic.websocket import AsyncJsonWebsocketConsumer

from django_core_micha.auth.ws_permissions import BaseSecureConsumer, IsAuthenticatedWs


class NotificationConsumer(BaseSecureConsumer, AsyncJsonWebsocketConsumer):
    permission_classes_ws = [IsAuthenticatedWs]

    async def post_connect(self):
        user = self.scope["user"]
        self.group_name = f"notifications_user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content):
        return

    async def message(self, event):
        await self.send_json(event["payload"])
