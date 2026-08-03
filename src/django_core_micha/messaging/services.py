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
        transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation), "participant_changed", {}))
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
        conversation, created = Conversation.objects.get_or_create(app=app, scope=scope, kind=Conversation.Kind.DIRECT, user_low=low, user_high=high)
        for user in (actor, target):
            ConversationParticipant.objects.get_or_create(conversation=conversation, user=user, defaults={"membership_source": "manual"})
        if created:
            # Re-opening an existing DM is a routine no-op (get_or_create finds the row);
            # only a genuinely new conversation is a create/open event worth a frame.
            transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation), "conversation_upsert", _conversation_upsert_payload(conversation)))
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
        reuse_by_external_key = kind in {
            Conversation.Kind.MANAGED,
            Conversation.Kind.GROUP,
            Conversation.Kind.BROADCAST,
        } and external_key is not None
        if reuse_by_external_key:
            conversation, created = Conversation.objects.get_or_create(
                app=app,
                scope=scope,
                kind=kind,
                external_key=external_key,
                defaults={"title": title},
            )
        else:
            conversation = Conversation.objects.create(app=app, scope=scope, kind=kind, title=title, external_key=external_key)
            created = True
        ConversationParticipant.objects.get_or_create(conversation=conversation, user=actor)
        for user in participant_users:
            if user.pk != actor.pk:
                ConversationParticipant.objects.get_or_create(conversation=conversation, user=user)
        if kind in {Conversation.Kind.MANAGED, Conversation.Kind.GROUP, Conversation.Kind.BROADCAST, Conversation.Kind.OBJECT_THREAD}:
            reconcile_membership(conversation=conversation, trigger="scope_created" if created else "reconcile")
        if created:
            # Re-opening an existing keyed conversation is a routine no-op; only a
            # genuinely new conversation is a create/open event worth a frame.
            transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation), "conversation_upsert", _conversation_upsert_payload(conversation)))
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
            existing = Message.objects.select_related("conversation__app", "sender").filter(conversation=conversation, sender=actor, client_request_id=client_request_id).first()
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
                return Message.objects.select_related("conversation__app", "sender").get(conversation=conversation, sender=actor, client_request_id=client_request_id), False
            raise
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message_at", "updated_at"])
        ConversationParticipant.objects.filter(conversation=conversation, removed_at__isnull=True).exclude(user=actor).update(archived_at=None)
        # Recipients are re-resolved live inside the on_commit callback (not
        # captured here) so a concurrent membership/mute change committed between
        # now and commit is reflected — consistent with edit_message/soft_delete_message.
        transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation, sender=actor), "message", {"message_id": str(message.id)}))
        transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation, sender=actor), "conversation_upsert", _conversation_upsert_payload(conversation)))
        transaction.on_commit(lambda: _notify_message(message, actor))
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
            message.body = message.title = message.link_target = None
            message.save(update_fields=["body", "title", "link_target", "deleted_at", "deleted_by", "updated_at"])
            transaction.on_commit(lambda: _publish(message.conversation, resolve_live_recipients(conversation=message.conversation), "message_deleted", {"message_id": str(message.id), "deleted_at": message.deleted_at.isoformat(), "deleted_by": str(actor.pk)}))
    return message


def add_reaction(*, actor, message, emoji):
    with transaction.atomic():
        _require_view(actor, message.conversation); _require_participant(actor, message.conversation)
        if not emoji or len(emoji) > 16:
            raise ValueError("Emoji must be between 1 and 16 characters.")
        reaction, added = MessageReaction.objects.get_or_create(message=message, user=actor, emoji=emoji)
        if added:
            transaction.on_commit(lambda: _publish(message.conversation, resolve_live_recipients(conversation=message.conversation), "reaction", {"message_id": str(message.id), "reactions": _serialized_reactions(message)}))
    return reaction


def remove_reaction(*, actor, message, emoji):
    with transaction.atomic():
        _require_view(actor, message.conversation); _require_participant(actor, message.conversation)
        deleted, _ = MessageReaction.objects.filter(message=message, user=actor, emoji=emoji).delete()
        if deleted:
            transaction.on_commit(lambda: _publish(message.conversation, resolve_live_recipients(conversation=message.conversation), "reaction", {"message_id": str(message.id), "reactions": _serialized_reactions(message)}))


def create_poll(*, actor, conversation, question, options, allow_multiple=False, client_request_id=None):
    if len(options) < 2:
        raise ValueError("A poll needs at least two options.")
    with transaction.atomic():
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
        # `option_ids` is the caller's complete, authoritative vote set for both single-
        # and multi-select polls -- not a delta. Previously only the single-choice branch
        # cleared prior votes; multi-select only ever added rows via `bulk_create`, so a
        # smaller `option_ids` set never retracted a previously-cast vote (untoggling an
        # option was silently a no-op). Delete anything no longer in the set, then
        # (re-)create the requested set; `ignore_conflicts` makes re-voting an
        # already-selected option a no-op rather than a duplicate-row error (unique
        # constraint on option+user).
        PollVote.objects.filter(option__poll=poll, user=actor).exclude(option_id__in=option_ids).delete()
        PollVote.objects.bulk_create([PollVote(option=option, user=actor) for option in options], ignore_conflicts=True)
        transaction.on_commit(lambda: _publish(poll.message.conversation, resolve_live_recipients(conversation=poll.message.conversation), "poll_updated", _poll_updated_payload(poll)))
    return poll


def close_poll(*, actor, poll):
    _require_view(actor, poll.message.conversation)
    _require_participant(actor, poll.message.conversation)
    rights = _policy(poll.message.conversation).moderation_rights(actor=actor, conversation=poll.message.conversation, message=poll.message)
    if poll.created_by_id != actor.pk and not rights.intersection({"edit_any", "delete_any"}):
        raise MessagingPermissionDenied("Closing is not permitted.")
    with transaction.atomic():
        if poll.closed_at is None:
            poll.closed_at = timezone.now(); poll.save(update_fields=["closed_at"])
            transaction.on_commit(lambda: _publish(poll.message.conversation, resolve_live_recipients(conversation=poll.message.conversation), "poll_updated", _poll_updated_payload(poll)))
    return poll


def update_conversation_config(*, actor, conversation, config):
    """Update a conversation scope's app-owned config after moderation approval."""
    _require_view(actor, conversation)
    rights = _policy(conversation).moderation_rights(actor=actor, conversation=conversation, message=None)
    if "manage_config" not in rights:
        raise MessagingPermissionDenied("Managing configuration is not permitted.")
    if not isinstance(config, dict):
        raise ValueError("config must be an object.")
    conversation.scope.config = {**conversation.scope.config, **config}
    conversation.scope.save(update_fields=["config", "updated_at"])
    return conversation.scope


def mark_read(*, actor, conversation, read_at=None):
    _require_view(actor, conversation); participant = _require_participant(actor, conversation)
    timestamp = min(read_at or timezone.now(), timezone.now())
    if participant.last_read_at is None or timestamp > participant.last_read_at:
        participant.last_read_at = timestamp; participant.save(update_fields=["last_read_at"])
        transaction.on_commit(lambda: _publish(conversation, resolve_live_recipients(conversation=conversation), "read_state", {"user_id": str(actor.pk), "last_read_at": timestamp.isoformat()}))
    return participant


def mark_thread_read(*, actor, root, read_at=None):
    _require_view(actor, root.conversation); _require_participant(actor, root.conversation)
    timestamp = min(read_at or timezone.now(), timezone.now())
    receipt = MessageThreadReceipt.objects.filter(root=root, user=actor).first()
    if receipt is None or timestamp > receipt.last_read_at:
        receipt, _ = MessageThreadReceipt.objects.update_or_create(root=root, user=actor, defaults={"last_read_at": timestamp})
        transaction.on_commit(lambda: _publish(root.conversation, resolve_live_recipients(conversation=root.conversation), "thread_read_state", {"user_id": str(actor.pk), "root_id": str(root.id), "last_read_at": timestamp.isoformat()}))
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
    transaction.on_commit(lambda: _publish(conversation, [actor], "conversation_archived", {"archived": bool(archived)}))
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
    # An empty recipient set is not "read by everyone" -- `.exclude(...).exists()` on an
    # empty queryset is vacuously False, which made a message nobody can receive report
    # `all_read: True` immediately on send.
    all_read = participants.exists() and not participants.exclude(last_read_at__gte=message.created_at).exists()
    result = {"all_read": all_read}
    rights = _policy(conversation).moderation_rights(actor=actor, conversation=conversation, message=message)
    if conversation.kind != Conversation.Kind.DIRECT and "read_receipt_detail" in rights:
        result["recipient_detail"] = list(participants.values("user_id", "last_read_at", "last_delivered_at"))
        counts = participants.aggregate(
            recipient_count=Count("pk"),
            read_count=Count("pk", filter=Q(last_read_at__gte=message.created_at)),
        )
        result["recipient_count"] = counts["recipient_count"]
        result["read_count"] = counts["read_count"]
    return result


def batch_read_status(*, actor, message_ids):
    """Same per-message aggregate as `read_status`, for many messages in a bounded
    number of queries. A message whose conversation the actor cannot view is
    silently absent from the result -- never a denied/null placeholder, since
    that would reveal the message id exists at all, which is more than the
    single-message endpoint's 404 reveals."""
    messages = list(
        Message.objects.filter(pk__in=message_ids).select_related("conversation__app", "sender")
    )
    conversations_by_id = {}
    for message in messages:
        conversations_by_id.setdefault(message.conversation_id, message.conversation)

    viewable_conversation_ids = {
        conversation_id
        for conversation_id, conversation in conversations_by_id.items()
        if get_messaging_policy(conversation.app.app_key).can_view_conversation(actor=actor, conversation=conversation)
    }

    messages_by_conversation = {}
    for message in messages:
        if message.conversation_id in viewable_conversation_ids:
            messages_by_conversation.setdefault(message.conversation_id, []).append(message)

    result = {}
    for conversation_id, conversation_messages in messages_by_conversation.items():
        conversation = conversations_by_id[conversation_id]
        rights = _policy(conversation).moderation_rights(actor=actor, conversation=conversation, message=None)
        gated = conversation.kind != Conversation.Kind.DIRECT and "read_receipt_detail" in rights
        participant_rows = list(
            conversation.participants.filter(removed_at__isnull=True).values("user_id", "last_read_at")
        )
        for message in conversation_messages:
            non_sender_rows = [row for row in participant_rows if row["user_id"] != message.sender_id]
            # Same vacuous-truth pattern as `read_status` above: `all()` over an empty
            # generator is True, and the old `else True` made it explicit either way.
            all_read = bool(non_sender_rows) and all(
                row["last_read_at"] is not None and row["last_read_at"] >= message.created_at
                for row in non_sender_rows
            )
            entry = {"all_read": all_read}
            if gated:
                entry["recipient_count"] = len(non_sender_rows)
                entry["read_count"] = sum(
                    1 for row in non_sender_rows
                    if row["last_read_at"] is not None and row["last_read_at"] >= message.created_at
                )
            result[str(message.id)] = entry
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


def _serialized_reactions(message):
    from .serializers import serialize_reactions
    message = Message.objects.prefetch_related("reactions").get(pk=message.pk)
    return serialize_reactions(message)


def _poll_updated_payload(poll):
    from .serializers import serialize_poll
    poll = Poll.objects.select_related("message__conversation__app").prefetch_related("options__votes").get(pk=poll.pk)
    return {"message_id": str(poll.message_id), "poll_id": str(poll.id), "poll": serialize_poll(poll)}


def _conversation_upsert_payload(conversation):
    from .serializers import serialize_conversation_core
    conversation = Conversation.objects.select_related("app", "scope").get(pk=conversation.pk)
    return serialize_conversation_core(conversation)


def _notify_message(message, sender):
    """Delivery failure is intentionally isolated from the durable message."""
    try:
        from .notifications import notify_message
        notify_message(message=message, recipients=resolve_live_recipients(conversation=message.conversation, sender=sender))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Messaging notification delivery failed for message %s", message.pk)
