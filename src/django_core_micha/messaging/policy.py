"""Consumer supplied authorization and membership hooks for messaging."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from django.contrib.auth import get_user_model

from .models import Conversation, Message, MessagingScope

User: TypeAlias = type(get_user_model())
ScopeRef: TypeAlias = MessagingScope
ConversationRef: TypeAlias = Conversation
MessageRef: TypeAlias = Message
ScopeConfig: TypeAlias = dict


@dataclass(frozen=True)
class MembershipSnapshot:
    members: Iterable[User]
    external_key: str | None = None
    remove_absent: bool = False


class MessagingPolicy(Protocol):
    def can_open_direct(self, *, actor: User, target: User, scope: ScopeRef | None) -> bool: ...
    def can_view_conversation(self, *, actor: User, conversation: ConversationRef) -> bool: ...
    def can_post(self, *, actor: User | None, conversation: ConversationRef, message_kind: str) -> bool: ...
    def moderation_rights(self, *, actor: User, conversation: ConversationRef, message: MessageRef | None = None) -> frozenset[str]: ...
    def resolve_recipients(self, *, conversation: ConversationRef, trigger: Literal["create", "message", "membership_refresh"]) -> Iterable[User]: ...
    def provision_membership(self, *, conversation: ConversationRef, trigger: Literal["scope_created", "domain_changed", "reconcile"]) -> MembershipSnapshot: ...
    def validate_scope(self, *, actor: User, scope: ScopeRef, conversation_kind: str) -> ScopeConfig: ...


_POLICIES: dict[str, MessagingPolicy] = {}


def register_messaging_policy(app_key: str, policy: MessagingPolicy) -> None:
    """Register exactly one policy for an app key.

    Replacing a policy at runtime would make authorization non-deterministic, so
    only re-registering the same object (useful for idempotent AppConfig.ready)
    is allowed.
    """
    if not isinstance(app_key, str) or not app_key.strip():
        raise ValueError("Messaging app keys must be non-empty strings.")
    existing = _POLICIES.get(app_key)
    if existing is not None and existing is not policy:
        raise ValueError(f"A messaging policy is already registered for {app_key!r}.")
    _POLICIES[app_key] = policy


def unregister_messaging_policy(app_key: str) -> None:
    """Test-only cleanup helper."""
    _POLICIES.pop(app_key, None)


def get_messaging_policy(app_key: str) -> MessagingPolicy:
    try:
        return _POLICIES[app_key]
    except KeyError as exc:
        raise LookupError(f"No messaging policy is registered for {app_key!r}.") from exc
