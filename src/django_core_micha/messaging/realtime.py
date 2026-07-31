"""Shared Layer-1 fan-out; deliberately no WebSocket consumer lives here."""
from __future__ import annotations

import uuid
from django.utils import timezone
from django_core_micha.notifications.delivery import push_to_users


def publish_messaging_event(*, conversation, users, event_type, payload):
    push_to_users(users, {"envelope": "messaging", "type": event_type, "event_id": str(uuid.uuid4()), "app_key": conversation.app.app_key, "conversation_id": str(conversation.id), "occurred_at": timezone.now().isoformat(), **payload})
