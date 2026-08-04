"""Consumer-supplied read-authorization hook for activity.

dcm must never learn what a scope object *is* or what "may manage this scope"
means for a given consumer (that is exactly the domain knowledge this WO forbids
dcm from acquiring) — so, mirroring messaging's `MessagingPolicy` pattern
(django_core_micha.messaging.policy), the read-permission decision is delegated
to a per-app-registered policy object. Recording (the ping endpoint) needs no
policy: it is always the acting user's own presence.

Fails CLOSED: `get_activity_policy` raises `LookupError` for an unregistered
app_key. The query view must treat that as a denial (403), never as an implicit
"allow" — do not default this open.
"""

from __future__ import annotations

from typing import Protocol, TypeAlias

from django.contrib.auth import get_user_model

User: TypeAlias = type(get_user_model())


class ActivityPolicy(Protocol):
    def can_read_activity(self, *, actor: User, app_key: str, content_type, object_id: str) -> bool: ...


_POLICIES: dict[str, ActivityPolicy] = {}


def register_activity_policy(app_key: str, policy: ActivityPolicy) -> None:
    """Register exactly one policy for an app key.

    Replacing a policy at runtime would make authorization non-deterministic, so
    only re-registering the same object (useful for idempotent AppConfig.ready)
    is allowed.
    """
    if not isinstance(app_key, str) or not app_key.strip():
        raise ValueError("Activity app keys must be non-empty strings.")
    existing = _POLICIES.get(app_key)
    if existing is not None and existing is not policy:
        raise ValueError(f"An activity policy is already registered for {app_key!r}.")
    _POLICIES[app_key] = policy


def unregister_activity_policy(app_key: str) -> None:
    """Test-only cleanup helper."""
    _POLICIES.pop(app_key, None)


def get_activity_policy(app_key: str) -> ActivityPolicy:
    try:
        return _POLICIES[app_key]
    except KeyError as exc:
        raise LookupError(f"No activity policy is registered for {app_key!r}.") from exc
