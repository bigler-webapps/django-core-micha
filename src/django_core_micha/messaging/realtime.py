"""Shared Layer-1 fan-out; deliberately no WebSocket consumer lives here."""
from __future__ import annotations

import uuid
from django.utils import timezone
from django_core_micha.notifications.delivery import push_to_users


def publish_messaging_event(*, conversation, users, event_type, payload):
    # Services schedule this only with transaction.on_commit.  The payload holds
    # opaque IDs; resolve the safe projection here, after commit, so plaintext is
    # never routed through logs or exception strings.
    frame = {"envelope": "messaging", "type": event_type, "event_id": str(uuid.uuid4()), "app_key": conversation.app.app_key, "conversation_id": str(conversation.id), "occurred_at": timezone.now().isoformat(), **payload}
    if "message_id" in payload and event_type in {"message", "message_edited"}:
        from .models import Message
        from .serializers import serialize_message
        message = Message.objects.select_related("conversation__app", "sender").prefetch_related("attachments", "reactions", "poll__options__votes").get(pk=payload["message_id"])
        frame["message"] = serialize_message(message)
    push_to_users(users, frame)
