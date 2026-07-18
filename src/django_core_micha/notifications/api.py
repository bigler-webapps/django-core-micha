"""Public authoring API for canonical notifications."""
from collections.abc import Iterable

from django.db import IntegrityError, transaction
from django.utils import timezone

from .dispatch import dispatch
from .models import Notification, NotificationDelivery, NotificationRecipient
from .router import resolve_channels
from .types import get_notification_type


def _normalize_recipients(recipients) -> list:
    if isinstance(recipients, Iterable) and not isinstance(recipients, (str, bytes)):
        candidates = list(recipients)
    else:
        candidates = [recipients]

    normalized = []
    seen = set()
    for user in candidates:
        marker = getattr(user, "pk", id(user))
        if marker not in seen:
            seen.add(marker)
            normalized.append(user)
    return normalized


def _get_notification_with_retry(*, notification_type, category, notifiable, content, urgency):
    dedup_key = Notification.build_dedup_key(notification_type, notifiable)
    try:
        with transaction.atomic():
            notification, _ = Notification.objects.get_or_create_by_dedup(
                notification_type=notification_type,
                category=category,
                notifiable=notifiable,
                content=content,
                urgency=urgency,
            )
            return notification
    except IntegrityError:
        return Notification.objects.get(dedup_key=dedup_key)


def notify(*, type, recipients, category=None, urgency="normal", content, notifiable=None, channels=None) -> Notification:
    """Create or reuse a logical message, then dispatch it per recipient and channel."""

    ntype = get_notification_type(type)
    if category is not None and category != ntype.category:
        raise ValueError(
            f"category={category!r} does not match the registered category "
            f"{ntype.category!r} for notification type {type!r}"
        )
    category = ntype.category
    notification = _get_notification_with_retry(
        notification_type=type,
        category=category,
        notifiable=notifiable,
        content=content,
        urgency=urgency,
    )

    for user in _normalize_recipients(recipients):
        recipient, _ = NotificationRecipient.objects.get_or_create(notification=notification, user=user)
        for channel in resolve_channels(ntype, user, override=channels):
            delivery, created = NotificationDelivery.objects.get_or_create(
                recipient=recipient,
                channel=channel,
                digest_threshold=None,
                defaults={"status": "pending"},
            )
            if not created:
                continue
            result = dispatch(channel, notification=notification, recipient=recipient)
            if result is True:
                delivery.status = "sent"
                delivery.sent_at = timezone.now()
                delivery.save(update_fields=["status", "sent_at"])
            elif result is False:
                delivery.status = "failed"
                delivery.save(update_fields=["status"])

    return notification
