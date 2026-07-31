"""Safe API projections for the messaging domain.

These functions are deliberately the only place REST/realtime turn encrypted
model fields into response data.  They never expose ciphertext, audit rows, or
storage keys.
"""
from rest_framework import serializers

from .models import Conversation, ConversationParticipant, Message, MessageReaction, Poll
from .crypto import decrypt_text


class MessageInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["chat", "announcement", "poll", "system"], default="chat")
    body = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    link_target = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    reply_to = serializers.UUIDField(required=False, allow_null=True)
    client_request_id = serializers.UUIDField(required=False, allow_null=True)


class PollInputSerializer(serializers.Serializer):
    """Explicit scalar types so a form/multipart-encoded QueryDict body (list-valued
    per field) cannot silently corrupt `question`/`options`/`allow_multiple` the way
    a bare `dict(request.data)` would."""
    question = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField(), min_length=2)
    allow_multiple = serializers.BooleanField(required=False, default=False)
    client_request_id = serializers.UUIDField(required=False, allow_null=True)


def serialize_attachment(attachment):
    # File access is always authenticated through the dedicated endpoint.
    base = f"/api/messaging/attachments/{attachment.id}/"
    return {"id": str(attachment.id), "content_type": attachment.content_type,
            "byte_size": attachment.byte_size, "order": attachment.order,
            "scan_state": attachment.scan_state, "url": base,
            "thumbnail_url": f"{base}thumbnail/" if attachment.thumbnail_key else None}


def serialize_reactions(message):
    grouped = {}
    for reaction in message.reactions.all():
        grouped.setdefault(reaction.emoji, 0)
        grouped[reaction.emoji] += 1
    return [{"emoji": emoji, "count": count} for emoji, count in grouped.items()]


def serialize_message(message):
    app_key = message.conversation.app.app_key
    return {
        "id": str(message.id), "conversation_id": str(message.conversation_id),
        "sender_id": message.sender_id, "kind": message.kind,
        "title": decrypt_text(app_key=app_key, value=message.title),
        "body": decrypt_text(app_key=app_key, value=message.body),
        "link_target": decrypt_text(app_key=app_key, value=message.link_target),
        "reply_to_id": str(message.reply_to_id) if message.reply_to_id else None,
        "client_request_id": str(message.client_request_id) if message.client_request_id else None,
        "edited_at": message.edited_at, "deleted_at": message.deleted_at,
        "created_at": message.created_at, "attachments": [serialize_attachment(a) for a in message.attachments.all()],
        "reactions": serialize_reactions(message),
    }


def serialize_conversation(conversation, participant):
    return {
        "id": str(conversation.id), "app_key": conversation.app.app_key,
        "scope_id": str(conversation.scope_id), "kind": conversation.kind,
        "title": decrypt_text(app_key=conversation.app.app_key, value=conversation.title), "last_message_at": conversation.last_message_at,
        "created_at": conversation.created_at, "archived_at": participant.archived_at,
        "muted": participant.muted, "email_enabled": participant.email_enabled,
        "push_enabled": participant.push_enabled,
    }
