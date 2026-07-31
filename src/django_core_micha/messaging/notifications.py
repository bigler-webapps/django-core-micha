"""Per-app registration and delivery recipe for messaging notifications."""
from datetime import timedelta

from django.utils import timezone

from django_core_micha.notifications.api import notify
from django_core_micha.notifications.types import NotificationType, get_notification_type, register_notification_type
from .crypto import decrypt_text


def messaging_notification_type_key(app_key):
    return f"{app_key}.messaging.new_message"


def register_messaging_notification_type(app_key):
    """Register an app-owned messaging event type from that app's ``ready()``.

    dcm never performs this registration implicitly: the consuming app owns its
    event key and delivery semantics.
    """
    key = messaging_notification_type_key(app_key)
    try:
        return get_notification_type(key)
    except LookupError:
        notification_type = NotificationType(
            key=key, category="messaging", mode="event", resolution="user-done",
            default_channels=["email", "push"], eligible_channels=["email", "push"],
            feed_visible=False,
        )
        register_notification_type(notification_type)
        return notification_type


def notify_message(*, message, recipients):
    """Send the safe durable projection; sensitive preview data stays transient."""
    app_key = message.conversation.app.app_key
    key = messaging_notification_type_key(app_key)
    try:
        get_notification_type(key)
    except LookupError:
        # An app which has not opted in has no messaging notification type.
        return None
    return notify(
        type=key, recipients=recipients, notifiable=message,
        content={"title_key": "messaging.new_message", "body_key": "messaging.new_message", "link": f"/messaging/conversations/{message.conversation_id}/", "params": {"message_id": str(message.id)}},
        transient={
            "title": decrypt_text(app_key=app_key, value=message.title),
            "body": decrypt_text(app_key=app_key, value=message.body),
        },
        expires_at=timezone.now() + timedelta(days=30),
    )
