from django.urls import re_path
from .consumers import ChatConsumer
websocket_urlpatterns=[re_path(r"^ws/assistant/chats/(?P<chat_id>[0-9a-f-]+)/$",ChatConsumer.as_asgi())]
