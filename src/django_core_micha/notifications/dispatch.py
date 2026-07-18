"""Minimal working channel dispatch for canonical notifications."""
import logging

from django.utils import translation
from django.utils.translation import gettext

from .delivery import _send_email, _send_push, push_to_users


logger = logging.getLogger(__name__)


def _recipient_language(user) -> str:
    try:
        profile = user.contact_profile
    except AttributeError:
        profile = None
    language = getattr(profile, "language", "de")
    return language if language in {"de", "en", "fr"} else "de"


def _render_content(content: dict, user) -> tuple[str, str, str]:
    """Render text for a recipient, falling back to source keys on bad translations."""

    title_key = str(content.get("title_key", ""))
    body_key = str(content.get("body_key", ""))
    params = content.get("params", {})
    params = params if isinstance(params, dict) else {}
    with translation.override(_recipient_language(user)):
        try:
            title = gettext(title_key).format(**params) if title_key else ""
        except Exception:
            title = title_key
        try:
            body = gettext(body_key).format(**params) if body_key else ""
        except Exception:
            body = body_key
    link = content.get("link", "")
    return title, body, link if isinstance(link, str) else ""


def dispatch(channel: str, *, notification, recipient):
    """Dispatch one channel, returning True/False or None for pending stub work."""

    user = recipient.user
    try:
        if channel == "chip":
            push_to_users(
                [user],
                {
                    "type": notification.notification_type,
                    "content": notification.content,
                    "notification_id": notification.pk,
                },
            )
            return True
        if channel in {"email", "push"}:
            title, body, url = _render_content(notification.content, user)
            sender = _send_email if channel == "email" else _send_push
            sender(title=title, body=body, url=url, users=[user], bypass_preference_check=True)
            return True
        if channel in {"todo", "popup"}:
            logger.info("Notification %s queued for unimplemented %s channel", notification.pk, channel)
            return None
        logger.warning("Notification %s has no dispatcher for channel %s", notification.pk, channel)
        return False
    except Exception:
        logger.exception("Notification %s dispatch failed for %s", notification.pk, channel)
        return False
