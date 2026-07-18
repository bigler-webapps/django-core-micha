"""Domain-agnostic WebSocket, browser-push, and email notification delivery."""
import json
import logging
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import NotificationPreference, PushSubscription

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - dependency is optional at import time
    webpush = None  # type: ignore[assignment]
    WebPushException = Exception  # type: ignore[misc, assignment]


logger = logging.getLogger(__name__)


def push_to_users(users, payload):
    """Send a payload to every recipient's isolated notification WS group."""
    recipients = list(users or [])
    if not recipients:
        return
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
    except Exception as exc:
        logger.warning("Notification channel layer unavailable: %s", exc)
        return
    if channel_layer is None:
        return

    for user in recipients:
        try:
            async_to_sync(channel_layer.group_send)(
                f"notifications_user_{user.id}",
                {"type": "message", "payload": payload},
            )
        except Exception as exc:
            logger.warning("WebSocket notification failed for user %s: %s", user.id, exc)


def deliver_push_email(*, title, body, url, users):
    """Deliver opted-in browser push and email without coupling to a domain."""
    recipients = list(users or [])
    if not recipients:
        return
    try:
        _send_email(title=title, body=body, url=url, users=recipients)
    except Exception as exc:  # Defensive isolation around future implementation changes.
        logger.warning("Email notification fan-out failed: %s", exc)
    try:
        _send_push(title=title, body=body, url=url, users=recipients)
    except Exception as exc:  # Defensive isolation around future implementation changes.
        logger.warning("Browser-push notification fan-out failed: %s", exc)


def _full_url(url):
    if not url or url.startswith(("http://", "https://")):
        return url
    return f"{getattr(settings, 'PUBLIC_ORIGIN', '').rstrip('/')}{url}"


def _send_email(*, title, body, url, users, bypass_preference_check=False):
    """Email recipients who explicitly opted in; isolate each recipient.

    ``bypass_preference_check`` is for callers (the notification router) that have
    already resolved channel eligibility themselves — it skips the redundant
    opt-in re-check here, which would otherwise silently drop a recipient the
    router deliberately forced past their opt-out for a critical notification.
    """
    recipients = list(users or [])
    if bypass_preference_check:
        opted_in_ids = {user.id for user in recipients}
    else:
        try:
            opted_in_ids = set(
                NotificationPreference.objects.filter(
                    user_id__in=[user.id for user in recipients], email_opt_in=True
                ).values_list("user_id", flat=True)
            )
        except Exception as exc:
            logger.warning("Could not resolve email notification preferences: %s", exc)
            return

    full_url = _full_url(url)
    text_body = f"{body}\n\n{full_url}".strip() if full_url else body
    html_body = f"<p>{escape(body)}</p>"
    if full_url:
        escaped_url = escape(full_url)
        html_body += f'<p><a href="{escaped_url}">{escaped_url}</a></p>'

    for user in recipients:
        if user.id not in opted_in_ids or not getattr(user, "email", ""):
            continue
        try:
            message = EmailMultiAlternatives(
                subject=title,
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
        except Exception as exc:
            logger.warning("Email notification failed for user %s: %s", user.id, exc)


def _send_push(*, title, body, url, users, bypass_preference_check=False):
    """Send browser push to opted-in subscriptions; remove expired ones.

    ``bypass_preference_check`` is for callers (the notification router) that have
    already resolved channel eligibility themselves — see ``_send_email``.
    """
    vapid_private = getattr(settings, "VAPID_PRIVATE_KEY", "")
    vapid_email = getattr(settings, "VAPID_CLAIM_EMAIL", "")
    if not vapid_private:
        return
    if webpush is None:
        logger.warning("pywebpush is not installed; browser-push delivery skipped")
        return

    recipients = list(users or [])
    try:
        if bypass_preference_check:
            subscriptions = PushSubscription.objects.filter(
                user_id__in=[user.id for user in recipients]
            )
        else:
            opted_in_ids = set(
                NotificationPreference.objects.filter(
                    user_id__in=[user.id for user in recipients], push_opt_in=True
                ).values_list("user_id", flat=True)
            )
            subscriptions = PushSubscription.objects.filter(user_id__in=opted_in_ids)
    except Exception as exc:
        logger.warning("Could not resolve browser-push subscriptions: %s", exc)
        return

    payload = json.dumps({"title": title, "body": body, "url": url})
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims={"sub": f"mailto:{vapid_email}"},
            )
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            if response is not None and getattr(response, "status_code", None) == 410:
                try:
                    PushSubscription.objects.filter(pk=subscription.pk).delete()
                except Exception as delete_exc:
                    logger.warning("Could not delete expired push subscription: %s", delete_exc)
            else:
                logger.warning("Browser-push notification failed for user %s: %s", subscription.user_id, exc)
        except Exception as exc:
            logger.warning("Browser-push notification failed for user %s: %s", subscription.user_id, exc)
