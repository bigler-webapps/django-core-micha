"""Public authoring API for canonical notifications."""
from collections.abc import Iterable

from django.db import IntegrityError, transaction
from django.utils import timezone

from .dispatch import dispatch
from .models import Notification, NotificationDelivery, NotificationRecipient
from .router import resolve_channels
from .subscriptions import resolve_category_subscribers
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


def _get_notification_with_retry(*, notification_type, category, notifiable, content, urgency, expires_at=None):
    dedup_key = Notification.build_dedup_key(notification_type, notifiable)
    try:
        with transaction.atomic():
            notification, _ = Notification.objects.get_or_create_by_dedup(
                notification_type=notification_type,
                category=category,
                notifiable=notifiable,
                content=content,
                urgency=urgency,
                expires_at=expires_at,
            )
            return notification
    except IntegrityError:
        try:
            return Notification.objects.get(
                dedup_key=dedup_key,
                resolved_at__isnull=True,
            )
        except Notification.DoesNotExist:
            # The row we raced against was resolved in the gap between our failed
            # insert and this lookup -- there is no open row to reuse, so start a
            # fresh episode instead of raising.
            with transaction.atomic():
                notification, _ = Notification.objects.get_or_create_by_dedup(
                    notification_type=notification_type,
                    category=category,
                    notifiable=notifiable,
                    content=content,
                    urgency=urgency,
                    expires_at=expires_at,
                )
                return notification


def _get_delivery_with_retry(*, recipient, channel):
    """Create the immediate-delivery row, recovering a concurrent NULL-threshold insert."""

    try:
        with transaction.atomic():
            return NotificationDelivery.objects.get_or_create(
                recipient=recipient,
                channel=channel,
                digest_threshold=None,
                defaults={"status": "pending"},
            )
    except IntegrityError:
        return (
            NotificationDelivery.objects.get(
                recipient=recipient,
                channel=channel,
                digest_threshold=None,
            ),
            False,
        )


def notify(
    *, type, recipients, category=None, urgency="normal", content, notifiable=None, channels=None,
    transient=None, expires_at=None,
) -> Notification:
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
        expires_at=expires_at,
    )

    for user in _normalize_recipients(recipients):
        recipient, _ = NotificationRecipient.objects.get_or_create(notification=notification, user=user)
        for channel in resolve_channels(ntype, user, override=channels):
            delivery, created = _get_delivery_with_retry(recipient=recipient, channel=channel)
            if not created:
                continue
            result = dispatch(channel, notification=notification, recipient=recipient, ctx=transient)
            if result is True:
                delivery.status = "sent"
                delivery.sent_at = timezone.now()
                delivery.save(update_fields=["status", "sent_at"])
            elif result is False:
                delivery.status = "failed"
                delivery.save(update_fields=["status"])

    return notification


def notify_subscribers(
    *, type, category=None, urgency="normal", content, content_is_shareable=False,
    notifiable=None, channels=None, transient=None, expires_at=None,
) -> Notification | None:
    """``notify()`` for a category resolved by subscription instead of an explicit
    recipient list (NOTIF-26 scope D).

    Resolves recipients from ``resolve_category_subscribers`` BEFORE anything is
    authored, and returns ``None`` without creating a ``Notification`` row when nobody
    subscribes (scope F) -- ``notify()`` itself authors its row before its recipient
    loop, so the empty-subscriber short-circuit has to happen one level up, here,
    rather than inside ``notify()``.

    ``content_is_shareable`` is a required, fail-closed acknowledgement (scope
    privacy risk): one ``content`` payload is durable and visible, unfiltered, to every
    subscriber via the canonical feed -- there is no per-recipient content today. A
    call site must explicitly confirm it carries nothing beyond what a wider,
    self-selected audience should see before this will deliver at all.
    """

    if not content_is_shareable:
        raise ValueError(
            "notify_subscribers() delivers one shared `content` payload to every "
            "subscriber of the category; pass content_is_shareable=True only after "
            "confirming content carries nothing the emitting call site would not want "
            "a wider, self-selected audience to see."
        )

    ntype = get_notification_type(type)
    resolved_category = category or ntype.category
    recipients = list(resolve_category_subscribers(resolved_category))
    if not recipients:
        return None

    return notify(
        type=type, recipients=recipients, category=category, urgency=urgency,
        content=content, notifiable=notifiable, channels=channels, transient=transient,
        expires_at=expires_at,
    )


def resolve(*, type, notifiable) -> Notification | None:
    """Close the open episode for ``type``+``notifiable``.

    Mark every not-yet-done recipient done and close the notification, so the
    next emit for the same (type, target) starts a fresh episode instead of
    silently reusing this one. Return ``None`` when there is no open
    notification to resolve.
    """

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            dedup_key=Notification.build_dedup_key(type, notifiable),
            resolved_at__isnull=True,
        ).first()
        if notification is None:
            return None
        NotificationRecipient.objects.filter(
            notification=notification,
            done_at__isnull=True,
        ).update(done_at=timezone.now())
        notification.mark_resolved()
    return notification


def has_open(*, type, notifiable) -> bool:
    """Return whether an open Notification exists for ``type``+``notifiable``."""

    return Notification.objects.get_open(type, notifiable) is not None
