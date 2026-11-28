"""Public authoring API for canonical notifications."""
from collections.abc import Iterable
from datetime import timedelta
from hashlib import sha256
import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .dispatch import dispatch_result
from .models import Notification, NotificationDelivery, NotificationPushActionToken, NotificationRecipient
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


def _same_origin_url(url):
    """Accept a relative URL or an absolute URL on this application's public origin."""

    if not url:
        return None
    if not isinstance(url, str):
        raise ValueError("push open URL must be a string")
    if "\\" in url or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("push open URL must be same-origin")
    parsed = urlsplit(url)
    if not parsed.scheme and not parsed.netloc and len(url) > 1 and url.startswith("/") and not url.startswith("//"):
        return url
    expected = urlsplit(getattr(settings, "PUBLIC_ORIGIN", ""))
    if not parsed.scheme or not parsed.netloc or (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
        raise ValueError("push open URL must be same-origin")
    return url


def _push_options(notification_type, *, push_tag=None, push_open_url=None):
    tag = push_tag if push_tag is not None else notification_type.push_tag
    if tag is not None and (not isinstance(tag, str) or not tag):
        raise ValueError("push tag must be a nonempty string")
    if notification_type.push_renotify and not tag:
        raise ValueError("push_renotify requires a nonempty tag")
    open_url = _same_origin_url(push_open_url) if push_open_url is not None else None
    labels = {}
    if notification_type.push_open_label_key:
        labels["open"] = notification_type.push_open_label_key
    if notification_type.push_done_label_key:
        labels["done"] = notification_type.push_done_label_key
    return {
        "icon": notification_type.push_icon,
        "badge": notification_type.push_badge,
        "tag": tag,
        "renotify": notification_type.push_renotify,
        "open_url": open_url,
        "action_labels": labels,
    }


def _dispatch_delivery(*, channel, notification, recipient, delivery, ctx):
    result = dispatch_result(channel, notification=notification, recipient=recipient, ctx=ctx)
    if result.ok is True:
        delivery.status = "sent"
        delivery.sent_at = timezone.now()
        delivery.save(update_fields=["status", "sent_at"])
    elif result.ok is False:
        delivery.status = "failed"
        delivery.save(update_fields=["status"])
    elif result.outcome is not None:
        delivery.status = result.outcome.value
        delivery.save(update_fields=["status"])


def notify(
    *, type, recipients, category=None, urgency="normal", content, notifiable=None, channels=None,
    transient=None, expires_at=None, push_tag=None, push_open_url=None,
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

    options = _push_options(ntype, push_tag=push_tag, push_open_url=push_open_url)
    for user in _normalize_recipients(recipients):
        recipient, _ = NotificationRecipient.objects.get_or_create(notification=notification, user=user)
        for channel in resolve_channels(ntype, user, override=channels):
            delivery, created = _get_delivery_with_retry(recipient=recipient, channel=channel)
            if not created:
                continue
            ctx = transient
            if channel == "push" and any(value is not None and value != {} for value in options.values()):
                action_options = dict(options)
                if "done" in action_options["action_labels"]:
                    cleartext = secrets.token_urlsafe(32)
                    with transaction.atomic():
                        NotificationPushActionToken.objects.create(
                            delivery=delivery,
                            recipient=recipient,
                            action="done",
                            token_hash=sha256(cleartext.encode("utf-8")).hexdigest(),
                            expires_at=timezone.now() + timedelta(days=14),
                        )
                        action_options["done_token"] = cleartext
                        ctx = {**(transient or {}), "push_options": action_options}
                        transaction.on_commit(
                            lambda channel=channel, notification=notification, recipient=recipient,
                            delivery=delivery, ctx=ctx: _dispatch_delivery(
                                channel=channel, notification=notification, recipient=recipient,
                                delivery=delivery, ctx=ctx,
                            )
                        )
                else:
                    ctx = {**(transient or {}), "push_options": action_options}
            if not (channel == "push" and "done" in options["action_labels"]):
                _dispatch_delivery(
                    channel=channel, notification=notification, recipient=recipient,
                    delivery=delivery, ctx=ctx,
                )

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
