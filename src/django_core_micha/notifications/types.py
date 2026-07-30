"""Code-first notification type policies registered by consuming applications."""
from dataclasses import dataclass


VALID_NOTIFICATION_MODES = frozenset({"event", "provider"})


@dataclass
class NotificationType:
    """Policy that determines how one notification type is routed and resolved."""

    key: str
    category: str
    mode: str
    resolution: str
    default_channels: list[str]
    eligible_channels: list[str]
    persist_until_done: bool = False
    critical: bool = False
    window: dict | None = None
    # Applies to persisted, event-authored rows only. The canonical feed derives todo
    # entries live from the todo registry, which never consults this flag, so setting it
    # False on a mode="provider" type has no effect.
    feed_visible: bool = True

    def __post_init__(self):
        if self.mode not in VALID_NOTIFICATION_MODES:
            raise ValueError(f"Unknown notification mode: {self.mode}")


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
