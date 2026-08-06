"""Code-first notification type policies registered by consuming applications."""
from dataclasses import dataclass


VALID_NOTIFICATION_MODES = frozenset({"event", "provider"})


@dataclass
class NotificationType:
    """Policy that determines how one notification type is routed and resolved.

    NOTIF-26: a type declares *reach*, never a concrete channel. ``active`` means the
    notification must reach the user (email/push -- which one fires is the user's own
    preference, resolved by ``prefs.is_channel_enabled``); ``passive`` means it may wait
    until the user looks (the ``chip`` surface). This is deliberately not a boolean: a
    type may be active-only, passive-only, or both -- all three occur in the estate
    today. Passive granularity is atomic: a type never picks between ``chip`` and
    ``popup``, because letting it pick would reintroduce the per-app channel
    enumeration this replaces.

    ``feed_visible`` is derived from ``passive`` (see ``feed_visible`` property below)
    -- it is no longer an independently-settable flag, so the two concepts cannot drift
    apart. This applies to persisted, event-authored rows only: the canonical feed
    derives todo entries live from the separate todo registry
    (``notifications/todo/registry.py``), which never consults reach or
    ``feed_visible`` at all -- todos are explicitly out of the reach axis, carry their
    own lifecycle (due/remind_before/persist_until_done/always_visible), and their
    dispatcher is a no-op stub.
    """

    key: str
    category: str
    mode: str
    resolution: str
    active: bool = False
    passive: bool = False
    persist_until_done: bool = False
    critical: bool = False
    window: dict | None = None

    def __post_init__(self):
        if self.mode not in VALID_NOTIFICATION_MODES:
            raise ValueError(f"Unknown notification mode: {self.mode}")
        if self.mode == "event" and not self.active and not self.passive:
            raise ValueError(
                f"Notification type {self.key!r} must declare active and/or passive reach."
            )

    @property
    def feed_visible(self) -> bool:
        """Whether this type's rows appear in the canonical feed -- derived from reach.

        Passive types are, by definition, surfaces the user only sees when present --
        the feed is exactly that. Active-only types (e.g. jg's messaging) reach out and
        are never merely shown. No effect for ``mode="provider"`` (todo) rows, which the
        feed derives live from the todo registry instead.
        """
        return self.passive

    @property
    def eligible_channels(self) -> list[str]:
        """Concrete channels this type's reach makes available to the router.

        This is the ONLY place a reach declaration becomes a channel list -- the type
        itself never names one. ``active`` always contributes both ``email`` and
        ``push``; which one actually fires for a given user is resolved by preference,
        not declared here.
        """
        channels = []
        if self.active:
            channels.extend(["email", "push"])
        if self.passive:
            channels.append("chip")
        return channels


_REGISTRY: dict[str, NotificationType] = {}


def register_notification_type(notification_type: NotificationType) -> None:
    """Register or replace a notification type policy during app startup."""

    _REGISTRY[notification_type.key] = notification_type


def get_notification_type(key: str) -> NotificationType:
    """Return a registered type policy, raising ``LookupError`` for unknown keys."""

    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise LookupError(f"Unknown notification type: {key}") from exc


def iter_feed_hidden_type_keys() -> set[str]:
    """Return registered notification types explicitly excluded from the canonical feed."""

    return {key for key, notification_type in _REGISTRY.items() if not notification_type.feed_visible}


def iter_registered_event_types() -> list[tuple[str, "NotificationType"]]:
    """Return (key, type) pairs for every registered ``mode="event"`` type.

    Excludes ``mode="provider"`` (todo) types -- they carry no reach declaration and
    are explicitly out of this axis (see ``NotificationType``'s docstring).
    """

    return [(key, ntype) for key, ntype in _REGISTRY.items() if ntype.mode == "event"]
