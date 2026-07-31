"""Transactional, app-neutral messaging domain operations."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import asdict

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .crypto import decrypt_text
from .models import (Conversation, ConversationParticipant, Message, MessageReaction,
                     MessageThreadReceipt, MessagingAuditEvent, MessagingScope, Poll, PollOption, PollVote)
from .policy import MembershipSnapshot, get_messaging_policy


class MessagingPermissionDenied(PermissionError):
    pass


def _policy(conversation):
    return get_messaging_policy(conversation.app.app_key)


def _participant(conversation, user, *, live=True):
    qs = ConversationParticipant.objects.filter(conversation=conversation, user=user)
    if live:
        qs = qs.filter(removed_at__isnull=True)
    return qs.first()


def _require_view(actor, conversation):
    if not _policy(conversation).can_view_conversation(actor=actor, conversation=conversation):
        raise MessagingPermissionDenied("Conversation access is not permitted.")


def _require_participant(actor, conversation):
    participant = _participant(conversation, actor)
    if participant is None:
        raise MessagingPermissionDenied("Current user is not a conversation participant.")
    return participant


def reconcile_membership(*, conversation, trigger="reconcile"):
    """Apply a provider snapshot, retaining removed rows as audit history."""
    policy = _policy(conversation)
    snapshot = policy.provision_membership(conversation=conversation, trigger=trigger)
    if not isinstance(snapshot, MembershipSnapshot):
        # A mapping form is deliberately accepted to make external providers simple.
        snapshot = MembershipSnapshot(**snapshot)
    members = {member.pk: member for member in snapshot.members}
    now = timezone.now()
    with transaction.atomic():
        if snapshot.external_key is not None and conversation.external_key != snapshot.external_key:
            conversation.external_key = snapshot.external_key
            conversation.save(update_fields=["external_key", "updated_at"])
        existing = {row.user_id: row for row in ConversationParticipant.objects.select_for_update().filter(conversation=conversation)}
        for user_id, member in members.items():
            row = existing.get(user_id)
            if row is None:
                # get_or_create (not create): select_for_update() above only locks
                # rows that already exist, so a brand-new conversation with no
                # existing participants gives two concurrent reconciles nothing to
                # serialize on for a shared new member — tolerate the resulting
                # unique-constraint race instead of raising.
                ConversationParticipant.objects.get_or_create(conversation=conversation, user=member, defaults={"membership_source": "provider"})
            elif row.membership_source == "provider" and row.removed_at is not None:
                row.removed_at = None
                row.save(update_fields=["removed_at"])
        if snapshot.remove_absent:
            ConversationParticipant.objects.filter(conversation=conversation, membership_source="provider", removed_at__isnull=True).exclude(user_id__in=members).update(removed_at=now)
    return snapshot


def open_direct(*, actor, target, app, scope=None):
    """Return the canonical direct pair, rejecting self-DMs in the core domain."""
    if actor.pk == target.pk:
        raise ValueError("A user cannot open a direct conversation with themselves.")
    scope = scope or MessagingScope.objects.get(app=app, kind=MessagingScope.Kind.GLOBAL)
    policy = get_messaging_policy(app.app_key)
    if not policy.can_open_direct(actor=actor, target=target, scope=scope):
        raise MessagingPermissionDenied("Opening a direct conversation is not permitted.")
    low, high = sorted((actor, target), key=lambda user: str(user.pk))
    with transaction.atomic():
        conversation, _ = Conversation.objects.get_or_create(app=app, scope=scope, kind=Conversation.Kind.DIRECT, user_low=low, user_high=high)
        for user in (actor, target):
            ConversationParticipant.objects.get_or_create(conversation=conversation, user=user, defaults={"membership_source": "manual"})
    return conversation


def create_conversation(*, actor, app, scope, kind, title=None, participant_users=(), external_key=None):
    """Create a non-direct conversation after app-owned scope validation."""
    if kind == Conversation.Kind.DIRECT:
        raise ValueError("Use open_direct for direct conversations.")
    policy = get_messaging_policy(app.app_key)
    policy.validate_scope(actor=actor, scope=scope, conversation_kind=kind)
    candidate = Conversation(app=app, scope=scope, kind=kind, external_key=external_key)
    rights = policy.moderation_rights(actor=actor, conversation=candidate, message=None)
    needed = {Conversation.Kind.GROUP: "open_group", Conversation.Kind.BROADCAST: "open_broadcast", Conversation.Kind.MANAGED: "create_managed"}.get(kind)
    if needed and needed not in rights:
        raise MessagingPermissionDenied("Opening this conversation type is not permitted.")
    if kind == Conversation.Kind.OBJECT_THREAD and scope.kind != MessagingScope.Kind.OBJECT:
        raise ValueError("Object threads require an object scope.")
    with transaction.atomic():
        conversation = Conversation.objects.create(app=app, scope=scope, kind=kind, title=title, external_key=external_key)
        ConversationParticipant.objects.create(conversation=conversation, user=actor)
        for user in participant_users:
            if user.pk != actor.pk:
                ConversationParticipant.objects.get_or_create(conversation=conversation, user=user)
        if kind in {Conversation.Kind.MANAGED, Conversation.Kind.OBJECT_THREAD}:
            reconcile_membership(conversation=conversation, trigger="scope_created")
    return conversation


def resolve_live_recipients(*, conversation, sender=None, trigger="message"):
    """Resolve policy users afresh and intersect with live, unmuted participants."""
    candidate_ids = {user.pk for user in _policy(conversation).resolve_recipients(conversation=conversation, trigger=trigger)}
    rows = ConversationParticipant.objects.select_related("user").filter(
        conversation=conversation, removed_at__isnull=True, muted=False, user_id__in=candidate_ids
    )
    return [row.user for row in rows if sender is None or row.user_id != sender.pk]


def send_message(*, actor, conversation, kind="chat", body=None, title=None, link_target=None, reply_to=None, client_request_id=None):
    """Durably create a message once; callers fan out only after this transaction commits."""
    with transaction.atomic():
        _require_view(actor, conversation)
        _require_participant(actor, conversation)
        if not _policy(conversation).can_post(actor=actor, conversation=conversation, message_kind=kind):
            raise MessagingPermissionDenied("Posting is not permitted.")
        if reply_to is not None:
            if reply_to.conversation_id != conversation.id or reply_to.reply_to_id is not None:
                raise ValueError("Replies must target a root message in the same conversation.")
        if client_request_id:
            existing = Message.objects.filter(conversation=conversation, sender=actor, client_request_id=client_request_id).first()
            if existing:
                return existing, False
        try:
            # Nested atomic() so a concurrent-retry IntegrityError only rolls back this
            # savepoint, not the whole outer transaction (Postgres aborts the entire
            # transaction on a bare IntegrityError, which would break the recovery
            # .get() below) — mirrors notifications/api.py's _get_notification_with_retry.
            with transaction.atomic():
                message = Message.objects.create(conversation=conversation, sender=actor, kind=kind, body=body, title=title, link_target=link_target, reply_to=reply_to, client_request_id=client_request_id)
        except IntegrityError:
            if client_request_id:
                return Message.objects.get(conversation=conversation, sender=actor, client_request_id=client_request_id), False
            raise
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message_at", "updated_at"])
        ConversationParticipant.objects.filter(conversation=conversation, removed_at__isnull=True).exclude(user=actor).update(archived_at=None)
        # Recipients are re-resolved live inside the on_commit callback (not
        # captured here) so a concurrent membership/mute change committed between
        # now and commit is reflected — consistent with edit_message/soft_delete_message.
        transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation, sender=actor), "message", {"message_id": str(message.id)}))
        return message, True


def edit_message(*, actor, message, body=None, title=None, link_target=None):
    with transaction.atomic():
        _require_view(actor, message.conversation)
        rights = _policy(message.conversation).moderation_rights(actor=actor, conversation=message.conversation, message=message)
        if message.sender_id != actor.pk and "edit_any" not in rights:
            raise MessagingPermissionDenied("Editing is not permitted.")
        if message.deleted_at:
            raise ValueError("Deleted messages cannot be edited.")
        message.body, message.title, message.link_target, message.edited_at = body, title, link_target, timezone.now()
        message.save(update_fields=["body", "title", "link_target", "edited_at", "updated_at"])
        transaction.on_commit(lambda: _publish(message.conversation, resolve_live_recipients(conversation=message.conversation), "message_edited", {"message_id": str(message.id)}))
    return message


def soft_delete_message(*, actor, message):
    with transaction.atomic():
        _require_view(actor, message.conversation)
        rights = _policy(message.conversation).moderation_rights(actor=actor, conversation=message.conversation, message=message)
        if message.sender_id != actor.pk and "delete_any" not in rights:
            raise MessagingPermissionDenied("Deletion is not permitted.")
        if message.deleted_at is None:
            message.deleted_at, message.deleted_by = timezone.now(), actor
            message.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
            transaction.on_commit(lambda: _publish(message.conversation, resolve_live_recipients(conversation=message.conversation), "message_deleted", {"message_id": str(message.id)}))
    return message


def add_reaction(*, actor, message, emoji):
    _require_view(actor, message.conversation); _require_participant(actor, message.conversation)
    if not emoji or len(emoji) > 16:
        raise ValueError("Emoji must be between 1 and 16 characters.")
    reaction, _ = MessageReaction.objects.get_or_create(message=message, user=actor, emoji=emoji)
    return reaction


def remove_reaction(*, actor, message, emoji):
    _require_view(actor, message.conversation); _require_participant(actor, message.conversation)
    MessageReaction.objects.filter(message=message, user=actor, emoji=emoji).delete()


def create_poll(*, actor, conversation, question, options, allow_multiple=False, client_request_id=None):
    if len(options) < 2:
        raise ValueError("A poll needs at least two options.")
    message, created = send_message(actor=actor, conversation=conversation, kind="poll", client_request_id=client_request_id)
    if not created and hasattr(message, "poll"):
        return message.poll, False
    poll = Poll.objects.create(message=message, question=question, allow_multiple=allow_multiple, created_by=actor)
    PollOption.objects.bulk_create([PollOption(poll=poll, text=text, order=index) for index, text in enumerate(options)])
    return poll, True


def vote_poll(*, actor, poll, option_ids):
    _require_view(actor, poll.message.conversation); _require_participant(actor, poll.message.conversation)
    if poll.closed_at:
        raise ValueError("Poll is closed.")
    options = list(poll.options.filter(id__in=option_ids))
    if len(options) != len(set(option_ids)) or (not poll.allow_multiple and len(options) != 1):
        raise ValueError("Invalid poll option selection.")
    with transaction.atomic():
        if not poll.allow_multiple:
            PollVote.objects.filter(option__poll=poll, user=actor).delete()
        PollVote.objects.bulk_create([PollVote(option=option, user=actor) for option in options], ignore_conflicts=True)


def close_poll(*, actor, poll):
    _require_view(actor, poll.message.conversation)
    rights = _policy(poll.message.conversation).moderation_rights(actor=actor, conversation=poll.message.conversation, message=poll.message)
    if poll.created_by_id != actor.pk and not rights.intersection({"edit_any", "delete_any"}):
        raise MessagingPermissionDenied("Closing is not permitted.")
    if poll.closed_at is None:
        poll.closed_at = timezone.now(); poll.save(update_fields=["closed_at"])
    return poll


def mark_read(*, actor, conversation, read_at=None):
    _require_view(actor, conversation); participant = _require_participant(actor, conversation)
    timestamp = min(read_at or timezone.now(), timezone.now())
    if participant.last_read_at is None or timestamp > participant.last_read_at:
        participant.last_read_at = timestamp; participant.save(update_fields=["last_read_at"])
    return participant


def mark_delivered(*, actor, conversation, delivered_at=None):
    _require_view(actor, conversation); participant = _require_participant(actor, conversation)
    timestamp = min(delivered_at or timezone.now(), timezone.now())
    if participant.last_delivered_at is None or timestamp > participant.last_delivered_at:
        participant.last_delivered_at = timestamp; participant.save(update_fields=["last_delivered_at"])
    return participant


def mark_thread_read(*, actor, root, read_at=None):
    _require_view(actor, root.conversation); _require_participant(actor, root.conversation)
    receipt, _ = MessageThreadReceipt.objects.update_or_create(root=root, user=actor, defaults={"last_read_at": min(read_at or timezone.now(), timezone.now())})
    return receipt


def set_preferences(*, actor, conversation, muted=None, email_enabled=None, push_enabled=None):
    _require_view(actor, conversation); participant = _require_participant(actor, conversation)
    updates = {k: v for k, v in {"muted": muted, "email_enabled": email_enabled, "push_enabled": push_enabled}.items() if v is not None}
    if updates:
        for key, value in updates.items(): setattr(participant, key, value)
        participant.save(update_fields=list(updates))
    return participant


def archive_conversation(*, actor, conversation, archived=True):
    _require_view(actor, conversation); participant = _require_participant(actor, conversation)
    participant.archived_at = timezone.now() if archived else None
    participant.save(update_fields=["archived_at"])
    return participant


def unread_counts(*, actor, app=None):
    qs = ConversationParticipant.objects.filter(user=actor, removed_at__isnull=True).select_related("conversation")
    if app is not None: qs = qs.filter(conversation__app=app)
    results = {}
    for p in qs:
        count = p.conversation.messages.filter(deleted_at__isnull=True).exclude(sender=actor).filter(Q(created_at__gt=p.last_read_at) if p.last_read_at else Q()).count()
        if count: results[str(p.conversation_id)] = count
    return {"unread_count": sum(results.values()), "by_conversation": results}


def read_status(*, actor, message):
    conversation = message.conversation; _require_view(actor, conversation)
    participants = conversation.participants.filter(removed_at__isnull=True).exclude(user=message.sender)
    delivered_count = participants.filter(last_delivered_at__gte=message.created_at).count()
    all_read = not participants.exclude(last_read_at__gte=message.created_at).exists()
    result = {"all_read": all_read, "delivered_count": delivered_count}
    rights = _policy(conversation).moderation_rights(actor=actor, conversation=conversation, message=message)
    if conversation.kind != Conversation.Kind.DIRECT and "read_receipt_detail" in rights:
        result["recipient_detail"] = list(participants.values("user_id", "last_read_at", "last_delivered_at"))
    return result


def break_glass_read(*, actor, message, reason, request_metadata=None, correlation_id=None):
    """Explicit exceptional content access, audited whether authorized or denied.

    `read_receipt_detail` grants aggregate/per-recipient READ-STATUS visibility
    (design doc, DM privacy carve-out); it does not grant CONTENT decryption.
    DMs are never break-glass-readable regardless of capability — the design's
    "DMs never expose recipient detail, including to moderators" invariant
    extends to content here, since there is no capability in the design's
    moderation-rights vocabulary that means "read this DM's plaintext".
    """
    conversation = message.conversation
    rights = _policy(conversation).moderation_rights(actor=actor, conversation=conversation, message=message)
    allowed = bool(reason and conversation.kind != Conversation.Kind.DIRECT and "read_receipt_detail" in rights)
    MessagingAuditEvent.objects.create(app=conversation.app, actor=actor, action="break_glass_read" if allowed else "break_glass_read_denied", target=message, reason=reason or "", request_metadata=request_metadata or {}, correlation_id=correlation_id)
    if not allowed:
        raise MessagingPermissionDenied("Break-glass access is not permitted.")
    app_key = conversation.app.app_key
    return {
        "title": decrypt_text(app_key=app_key, value=message.title),
        "body": decrypt_text(app_key=app_key, value=message.body),
        "link_target": decrypt_text(app_key=app_key, value=message.link_target),
    }


def _publish(conversation, users, event_type, payload):
    """The views chunk supplies safe serializers; this emits only opaque IDs."""
    from .realtime import publish_messaging_event
    publish_messaging_event(conversation=conversation, users=users, event_type=event_type, payload=payload)
