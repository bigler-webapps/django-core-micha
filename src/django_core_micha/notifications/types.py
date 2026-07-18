"""Code-first notification type policies registered by consuming applications."""
from dataclasses import dataclass


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
