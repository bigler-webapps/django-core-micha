"""Channel dispatcher registry for canonical notifications."""
from dataclasses import dataclass
import logging
from typing import ClassVar, Protocol

from .delivery import _send_email, _send_push, notification_envelope, push_to_users
from .models import NotificationPreference
from .text_registry import resolve_notification_text


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


def _resolve_text_key(key: str, language: str, params: dict, *, kind: str, require_registered: bool = False) -> str:
    """Render one key for one language, falling back to the raw key.

    A caller that opted in via ``require_registered`` (messaging's i18n keys) is
    asserting the key MUST resolve through the text registry -- a miss there is the
    exact MSG-13 defect (``gettext`` silently returning the msgid for a catalogue that
    doesn't exist), so it is logged loudly. A caller that never registered anything
    keeps the pre-existing contract: the key itself IS the format template (this is how
    every non-messaging notification type already authors its content).

    A future caller that registers real i18n keys but forgets ``require_registered_text``
    on its content dict would reproduce the exact MSG-13 defect silently -- when adding a
    new registry-backed notification type, set that flag.
    """

    if not key:
        return ""
    template = resolve_notification_text(key, language)
    if template is None:
        if require_registered:
            logger.warning("Notification text key %r has no registered translation; rendering the raw key", key)
        template = key
    try:
        return template.format(**params)
    except Exception:
        logger.warning("Notification %s rendering failed; falling back to source key", kind, exc_info=True)
        return key


def _render_content(content: dict, user, transient=None) -> tuple[str, str, str]:
    """Render text for a recipient. ``content["require_registered_text"]`` opts a
    caller into the text-registry i18n path (see ``_resolve_text_key``)."""

    title_key = str(content.get("title_key", ""))
    body_key = str(content.get("body_key", ""))
    params = content.get("params", {})
    params = params if isinstance(params, dict) else {}
    params = {**params, **(transient or {})}
    require_registered = bool(content.get("require_registered_text"))
    language = _recipient_language(user)
    title = _resolve_text_key(title_key, language, params, kind="title", require_registered=require_registered)
    body = _resolve_text_key(body_key, language, params, kind="body", require_registered=require_registered)
    link = content.get("link", "")
    return title, body, link if isinstance(link, str) else ""


def _push_preview_enabled(user) -> bool:
    """Whether ``user`` wants sender/text preview in a push body. Defaults to on --
    an existing ``NotificationPreference`` row predating this field, or no row at all,
    behaves as on."""

    preference = NotificationPreference.objects.filter(user=user).values_list(
        "push_preview_opt_in", flat=True
    ).first()
    return True if preference is None else preference


class ChipDispatcher:
    channel = "chip"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        push_to_users(
            [recipient.user],
            notification_envelope({
                "type": notification.notification_type,
                "content": notification.content,
                "notification_id": notification.pk,
                "recipient_id": recipient.pk,
                "channel": self.channel,
            }),
        )
        return DeliveryResult(ok=True)


class EmailDispatcher:
    channel = "email"

    def deliver(self, notification, recipient, ctx=None) -> DeliveryResult:
        title, body, url = _render_content(notification.content, recipient.user, ctx)
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
        content = notification.content
        hidden_body_key = content.get("hidden_body_key")
        if hidden_body_key and not _push_preview_enabled(recipient.user):
            content = {**content, "body_key": hidden_body_key}
        title, body, url = _render_content(content, recipient.user, ctx)
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
        push_to_users(
            [recipient.user],
            notification_envelope({
                "type": notification.notification_type,
                "content": notification.content,
                "notification_id": notification.pk,
                "recipient_id": recipient.pk,
                "channel": self.channel,
            }),
        )
        return DeliveryResult(ok=True)


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


def dispatch(channel: str, *, notification, recipient, ctx=None) -> bool | None:
    """Dispatch one channel, returning success, failure, or pending-stub status."""

    dispatcher = get_dispatcher(channel)
    if dispatcher is None:
        logger.warning("Notification %s has no dispatcher for channel %s", notification.pk, channel)
        return False

    for attempt in range(_MAX_DELIVERY_ATTEMPTS):
        try:
            result = dispatcher.deliver(notification, recipient, ctx)
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
