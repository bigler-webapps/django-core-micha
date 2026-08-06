"""Per-app registration and delivery recipe for messaging notifications."""
from datetime import timedelta

from django.utils import timezone

from django_core_micha.notifications.api import notify
from django_core_micha.notifications.types import NotificationType, get_notification_type, register_notification_type
from .crypto import decrypt_text
from .notification_texts import (
    NEW_MESSAGE_BODY_HIDDEN_KEY,
    NEW_MESSAGE_BODY_KEY_BY_KIND,
    NEW_MESSAGE_SETTINGS_LABEL_KEY,
    NEW_MESSAGE_TITLE_KEY,
    NEW_MESSAGE_TITLE_UNKNOWN_SENDER_KEY,
    register_messaging_notification_texts,
)
from .serializers import CONVERSATION_EXCERPT_LENGTH, _serialize_sender


def messaging_notification_type_key(app_key):
    return f"{app_key}.messaging.new_message"


def register_messaging_notification_type(app_key):
    """Register an app-owned messaging event type from that app's ``ready()``.

    dcm never performs this registration implicitly: the consuming app owns its
    event key and delivery semantics.
    """
    key = messaging_notification_type_key(app_key)
    register_messaging_notification_texts()
    try:
        return get_notification_type(key)
    except LookupError:
        notification_type = NotificationType(
            key=key, category="messaging", mode="event", resolution="user-done",
            # NOTIF-26: active-only reach -- messaging must reach the user (email/push,
            # the user's own choice of which), never merely wait in a passive surface.
            # feed_visible is now derived from reach (see NotificationType.feed_visible);
            # active-only correctly derives to feed-hidden, matching the previous
            # explicit feed_visible=False.
            active=True, passive=False,
            label_key=NEW_MESSAGE_SETTINGS_LABEL_KEY,
        )
        register_notification_type(notification_type)
        return notification_type


def _truncate_excerpt(text):
    """Cut ``text`` to the shared preview length with a trailing ellipsis, so a long
    push body is deliberately clipped by the product, not silently by the OS."""

    if len(text) <= CONVERSATION_EXCERPT_LENGTH:
        return text
    return text[: CONVERSATION_EXCERPT_LENGTH - 1].rstrip() + "…"


def _message_body_kind(message, decrypted_body):
    """Which body template a message needs -- mirrors ``serializers.serialize_last_message``'s
    per-kind/soft-delete handling, one level down (push text instead of list excerpt)."""

    if message.deleted_at is not None:
        return "deleted"
    if message.kind == message.Kind.POLL:
        return "poll"
    if message.kind == message.Kind.ANNOUNCEMENT:
        return "announcement"
    if message.kind == message.Kind.SYSTEM:
        return "system"
    if not decrypted_body.strip() and message.attachments.exists():
        return "attachment"
    return "chat"


def notify_message(*, message, recipients):
    """Send the safe durable projection; sensitive preview data stays transient.

    Title is the sender's name, body is the message text itself (or a translated
    per-kind fallback for content that has no text of its own) -- per the operator's
    2026-08-04 ruling on MSG-13. ``content["params"]`` (durable, persisted on the
    ``Notification`` row) carries only ``message_id``; sender name and message text
    live in ``transient`` only, which ``notify()`` never persists.
    """
    app_key = message.conversation.app.app_key
    key = messaging_notification_type_key(app_key)
    try:
        get_notification_type(key)
    except LookupError:
        # An app which has not opted in has no messaging notification type.
        return None

    register_messaging_notification_texts()

    sender = _serialize_sender(message)
    sender_name = sender["display_name"] if sender else None
    title_key = NEW_MESSAGE_TITLE_KEY if sender_name else NEW_MESSAGE_TITLE_UNKNOWN_SENDER_KEY

    decrypted_body = "" if message.deleted_at is not None else (decrypt_text(app_key=app_key, value=message.body) or "")
    body_kind = _message_body_kind(message, decrypted_body)
    body_key = NEW_MESSAGE_BODY_KEY_BY_KIND[body_kind]

    transient = {}
    if sender_name:
        transient["sender"] = sender_name
    if body_kind == "chat":
        transient["excerpt"] = _truncate_excerpt(decrypted_body)

    return notify(
        type=key, recipients=recipients, notifiable=message,
        content={
            "title_key": title_key,
            "body_key": body_key,
            "hidden_body_key": NEW_MESSAGE_BODY_HIDDEN_KEY,
            "require_registered_text": True,
            "link": f"/messaging/conversations/{message.conversation_id}/",
            "params": {"message_id": str(message.id)},
        },
        transient=transient,
        expires_at=timezone.now() + timedelta(days=30),
    )
