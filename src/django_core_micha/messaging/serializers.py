"""Safe API projections for the messaging domain.

These functions are deliberately the only place REST/realtime turn encrypted
model fields into response data.  They never expose ciphertext, audit rows, or
storage keys.
"""
from django.db.models import Count, Max
from rest_framework import serializers

from .models import Poll
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


CONVERSATION_EXCERPT_LENGTH = 140


def serialize_poll(poll):
    """Viewer-independent poll projection safe for REST and realtime."""
    app_key = poll.message.conversation.app.app_key
    return {
        "id": str(poll.id), "question": decrypt_text(app_key=app_key, value=poll.question),
        "allow_multiple": poll.allow_multiple, "closed_at": poll.closed_at,
        "created_by_id": poll.created_by_id,
        "options": [{
            "id": str(option.id), "text": decrypt_text(app_key=app_key, value=option.text),
            "order": option.order, "vote_count": len(option.votes.all()),
            "voters": [vote.user_id for vote in option.votes.all()],
        } for option in poll.options.all()],
    }


def _reply_stats(message):
    # A queryset-level `.annotate(reply_count=..., last_reply_at=...)` (see views.py's
    # list endpoints) sets these as real attributes on the instance, making this free;
    # single-object fetches fall back to one small aggregate query here. Either way,
    # a soft-deleted reply still counts — the row survives deletion as a tombstone, so
    # excluding it would undercount against what the thread actually renders.
    if hasattr(message, "reply_count"):
        return message.reply_count, message.last_reply_at
    agg = message.replies.aggregate(count=Count("id"), last=Max("created_at"))
    return agg["count"], agg["last"]


def serialize_message(message):
    app_key = message.conversation.app.app_key
    reply_count, last_reply_at = _reply_stats(message)
    result = {
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
        "reply_count": reply_count, "last_reply_at": last_reply_at,
    }
    if message.kind == "poll":
        try:
            result["poll"] = serialize_poll(message.poll)
        except Poll.DoesNotExist:
            pass
    return result


def serialize_last_message(conversation):
    message = conversation.messages.select_related("conversation__app", "poll").order_by("-created_at", "-id").first()
    if message is None:
        return None
    app_key = conversation.app.app_key
    if message.deleted_at is not None:
        excerpt = ""
    elif message.kind == "poll":
        try:
            excerpt = decrypt_text(app_key=app_key, value=message.poll.question)
        except Poll.DoesNotExist:
            excerpt = ""
    else:
        excerpt = decrypt_text(app_key=app_key, value=message.body) or ""
    return {"id": str(message.id), "sender_id": message.sender_id, "kind": message.kind,
            "excerpt": excerpt[:CONVERSATION_EXCERPT_LENGTH], "created_at": message.created_at}


def serialize_conversation_core(conversation):
    return {
        "id": str(conversation.id), "app_key": conversation.app.app_key,
        "scope_id": str(conversation.scope_id), "kind": conversation.kind,
        "title": decrypt_text(app_key=conversation.app.app_key, value=conversation.title),
        "last_message_at": conversation.last_message_at,
        "last_message": serialize_last_message(conversation), "created_at": conversation.created_at,
        "external_key": conversation.external_key,
    }


def serialize_conversation(conversation, participant):
    return {
        **serialize_conversation_core(conversation), "archived_at": participant.archived_at,
        "muted": participant.muted, "email_enabled": participant.email_enabled,
        "push_enabled": participant.push_enabled,
    }
