"""Channel dispatcher registry for canonical notifications."""
from dataclasses import dataclass
import logging
from typing import ClassVar, Protocol

from django.utils import translation
from django.utils.translation import gettext

from .delivery import _send_email, _send_push, push_to_users


logger = logging.getLogger(__name__)

_MAX_DELIVERY_ATTEMPTS = 3


@dataclass(frozen=True)
class DeliveryResult:
    """The outcome reported by one channel delivery attempt."""

    ok: bool | None
    error: str | None = None
    retryable: bool = False


class Dispatcher(Protocol):
    """Interface implemented by every notification delivery channel."""

    channel: ClassVar[str]

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        """Deliver one notification to one recipient."""


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


class ChipDispatcher:
    channel = "chip"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        push_to_users(
            [recipient.user],
            {
                "type": notification.notification_type,
                "content": notification.content,
                "notification_id": notification.pk,
            },
        )
        return DeliveryResult(ok=True)


class EmailDispatcher:
    channel = "email"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        title, body, url = _render_content(notification.content, recipient.user)
        _send_email(
            title=title,
            body=body,
            url=url,
            users=[recipient.user],
            bypass_preference_check=True,
        )
        return DeliveryResult(ok=True)


class PushDispatcher:
    channel = "push"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        title, body, url = _render_content(notification.content, recipient.user)
        _send_push(
            title=title,
            body=body,
            url=url,
            users=[recipient.user],
            bypass_preference_check=True,
        )
        return DeliveryResult(ok=True)


class TodoDispatcher:
    channel = "todo"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        logger.info("Notification %s queued for unimplemented todo channel", notification.pk)
        return DeliveryResult(ok=None, error="pending")


class PopupDispatcher:
    channel = "popup"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        logger.info("Notification %s queued for unimplemented popup channel", notification.pk)
        return DeliveryResult(ok=None, error="pending")


_DISPATCHERS: dict[str, Dispatcher] = {
    dispatcher.channel: dispatcher
    for dispatcher in (
        ChipDispatcher(),
        EmailDispatcher(),
        PushDispatcher(),
        TodoDispatcher(),
        PopupDispatcher(),
    )
}


def get_dispatcher(channel: str) -> Dispatcher | None:
    """Return the singleton dispatcher registered for ``channel``."""

    return _DISPATCHERS.get(channel)


def dispatch(channel: str, *, notification, recipient) -> bool | None:
    """Dispatch one channel, returning success, failure, or pending-stub status."""

    dispatcher = get_dispatcher(channel)
    if dispatcher is None:
        logger.warning("Notification %s has no dispatcher for channel %s", notification.pk, channel)
        return False

    for attempt in range(_MAX_DELIVERY_ATTEMPTS):
        try:
            result = dispatcher.deliver(notification, recipient)
        except Exception:
            logger.exception("Notification %s dispatch failed for %s", notification.pk, channel)
            return False

        # Retry applies only to an explicit ok=False, retryable=True transient failure.
        # ok=True (sent) and ok=None (pending stub, e.g. todo/popup) both return immediately.
        if result.ok is not False or not result.retryable:
            return result.ok
        if attempt + 1 < _MAX_DELIVERY_ATTEMPTS:
            logger.warning(
                "Notification %s dispatch retry %s/%s for %s: %s",
                notification.pk,
                attempt + 1,
                _MAX_DELIVERY_ATTEMPTS,
                channel,
                result.error or "retryable failure",
            )

    return False
