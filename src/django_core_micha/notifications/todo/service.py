"""Materialize live todo providers into canonical notification status rows."""
from dataclasses import dataclass, replace
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from ..models import Notification, NotificationRecipient
from .engine import TODO_TIMEZONE, materialize_todo, resolve_due_date
from .models import TodoOverride
from .registry import get_todo_config, get_todo_provider, iter_registered_todo_types


@dataclass
class _TodoCandidate:
    dedup_key: str
    recipient_id: Any
    content: dict
    notification_type: str
    notifiable_content_type: ContentType | None
    notifiable_object_id: str | None
    due_at: Any


def _apply_lead_override(config, override):
    if override is not None and config.lead_adjustable and override.lead_days_override is not None:
        return replace(config, remind_before=f"P{override.lead_days_override}D")
    return config


def _engine_config(config) -> dict:
    return {
        "due": config.due,
        "remindBefore": config.remind_before,
        "severity": config.severity,
        "persistUntilDone": config.persist_until_done,
        "alwaysVisible": config.always_visible,
    }


def _materialized_candidates(user, now) -> list[_TodoCandidate]:
    """Resolve provider output and overrides without touching notification overlays."""

    raw_seeds = []
    scoped_objects = []
    notifiable_objects = []
    for type_key in iter_registered_todo_types():
        provider_fn = get_todo_provider(type_key)
        config = get_todo_config(type_key)
        for seed in provider_fn(user, now):
            if seed.type_key != type_key:
                raise ValueError(f"Todo provider {type_key!r} emitted seed for {seed.type_key!r}")
            scope = seed.scope if seed.scope is not None else seed.notifiable
            raw_seeds.append((seed, config, scope))
            if scope is not None:
                scoped_objects.append(scope)
            if seed.notifiable is not None:
                notifiable_objects.append(seed.notifiable)

    content_types = ContentType.objects.get_for_models(
        *(type(instance) for instance in [*scoped_objects, *notifiable_objects])
    )
    scope_keys = {
        (content_types[type(scope)].pk, str(scope.pk), seed.type_key)
        for seed, _, scope in raw_seeds
        if scope is not None
    }
    overrides = {
        (override.content_type_id, override.object_id, override.type_key): override
        for override in TodoOverride.objects.filter(
            content_type_id__in={key[0] for key in scope_keys},
            object_id__in={key[1] for key in scope_keys},
            type_key__in={key[2] for key in scope_keys},
        )
    } if scope_keys else {}

    candidates = []
    for seed, config, scope in raw_seeds:
        override = overrides.get(
            (content_types[type(scope)].pk, str(scope.pk), seed.type_key)
        ) if scope is not None else None
        if override is not None and not override.enabled:
            continue
        effective_engine_config = _engine_config(_apply_lead_override(config, override))
        due_base_resolver = seed.due_base_resolver or (lambda base: None)
        materialized = materialize_todo(
            seed.content,
            effective_engine_config,
            now,
            due_base_resolver=due_base_resolver,
            has_due_time=seed.has_due_time,
            tz=TODO_TIMEZONE,
        )
        if materialized is None:
            continue
        content_type = content_types[type(seed.notifiable)] if seed.notifiable is not None else None
        candidates.append(_TodoCandidate(
            dedup_key=Notification.build_dedup_key(seed.type_key, seed.notifiable),
            recipient_id=seed.recipient.pk,
            content=materialized,
            notification_type=seed.type_key,
            notifiable_content_type=content_type,
            notifiable_object_id=str(seed.notifiable.pk) if seed.notifiable is not None else None,
            due_at=resolve_due_date(effective_engine_config["due"], due_base_resolver, TODO_TIMEZONE),
        ))
    return candidates


def _latest_candidates_by_dedup(candidates):
    """Match the previous per-seed loop: later duplicate seeds win the live projection."""

    return {candidate.dedup_key: candidate for candidate in candidates}


def derive_todos_for_user(user, now=None) -> list[NotificationRecipient]:
    """Materialize all currently emitted, visible provider todos for one user.

    The notification and recipient writes are the persistent per-user status overlay.
    They are deliberately retained, but resolved in bounded set queries.  Conflict-tolerant
    inserts keep the database uniqueness constraints as the TOCTOU backstop; a post-insert
    fetch makes a competing derivation converge on the row which won that race.
    """

    candidates = _materialized_candidates(user, now or timezone.now())
    if not candidates:
        return []
    latest_by_dedup = _latest_candidates_by_dedup(candidates)
    existing_notifications = {
        notification.dedup_key: notification
        for notification in Notification.objects.filter(dedup_key__in=latest_by_dedup)
    }
    missing_notifications = [
        Notification(
            dedup_key=candidate.dedup_key,
            notification_type=candidate.notification_type,
            category="todo",
            urgency="normal",
            content=candidate.content,
            content_type=candidate.notifiable_content_type,
            object_id=candidate.notifiable_object_id,
        )
        for dedup_key, candidate in latest_by_dedup.items()
        if dedup_key not in existing_notifications
    ]
    if missing_notifications:
        # ignore_conflicts is essential: another request may create one or more
        # dedup rows between the set lookup above and this bulk insert.
        Notification.objects.bulk_create(missing_notifications, ignore_conflicts=True)
        existing_notifications = {
            notification.dedup_key: notification
            for notification in Notification.objects.filter(dedup_key__in=latest_by_dedup)
        }
    stale_notifications = []
    for dedup_key, candidate in latest_by_dedup.items():
        notification = existing_notifications[dedup_key]
        if notification.content != candidate.content:
            notification.content = candidate.content
            stale_notifications.append(notification)
    if stale_notifications:
        Notification.objects.bulk_update(stale_notifications, ["content"])

    candidate_by_pair = {
        (existing_notifications[candidate.dedup_key].pk, candidate.recipient_id): candidate
        for candidate in candidates
    }
    notification_ids = {pair[0] for pair in candidate_by_pair}
    recipient_ids = {pair[1] for pair in candidate_by_pair}
    existing_recipients = {
        (recipient.notification_id, recipient.user_id): recipient
        for recipient in NotificationRecipient.objects.filter(
            notification_id__in=notification_ids,
            user_id__in=recipient_ids,
        )
    }
    missing_recipients = [
        NotificationRecipient(notification_id=notification_id, user_id=recipient_id)
        for notification_id, recipient_id in candidate_by_pair
        if (notification_id, recipient_id) not in existing_recipients
    ]
    if missing_recipients:
        # This unique pair has the same race shape as notification.dedup_key.
        NotificationRecipient.objects.bulk_create(missing_recipients, ignore_conflicts=True)
        existing_recipients = {
            (recipient.notification_id, recipient.user_id): recipient
            for recipient in NotificationRecipient.objects.filter(
                notification_id__in=notification_ids,
                user_id__in=recipient_ids,
            )
        }

    emitted = {}
    for candidate in candidates:
        notification = existing_notifications[candidate.dedup_key]
        notification._todo_due_at = candidate.due_at
        recipient = existing_recipients[(notification.pk, candidate.recipient_id)]
        recipient.notification = notification
        emitted[notification.pk] = recipient
    return sorted(emitted.values(), key=lambda recipient: recipient.notification.created_at, reverse=True)


def count_active_todos_for_user(user, now=None) -> int:
    """Count the live actionable projection without creating notification overlays.

    This uses the same seed, override, and materialization path as the feed. Missing
    overlay rows are actionable by definition and therefore count as unseen.
    """

    candidates = _materialized_candidates(user, now or timezone.now())
    latest_by_dedup = _latest_candidates_by_dedup(candidates)
    if not latest_by_dedup:
        return 0
    recipient_ids = {candidate.recipient_id for candidate in latest_by_dedup.values()}
    overlays = {
        (recipient.notification.dedup_key, recipient.user_id): recipient
        for recipient in NotificationRecipient.objects.filter(
            notification__dedup_key__in=latest_by_dedup,
            user_id__in=recipient_ids,
        ).select_related("notification")
    }
    return sum(
        overlay is None or (overlay.dismissed_at is None and overlay.done_at is None and overlay.seen_at is None)
        for candidate in latest_by_dedup.values()
        for overlay in [overlays.get((candidate.dedup_key, candidate.recipient_id))]
    )


def derive_active_todos(user, now=None) -> list[NotificationRecipient]:
    """Return the live, actionable todo recipient projection for one user.

    Dismissed and done rows remain in the persistent status overlay but are deliberately
    omitted from the live feed projection while their provider continues to emit them.
    """

    return [
        recipient
        for recipient in derive_todos_for_user(user, now)
        if recipient.dismissed_at is None and recipient.done_at is None
    ]


def sync_todos_for_user(user, now=None) -> list[Notification]:
    """Backward-compatible notification-shaped view of live actionable todos."""

    return [recipient.notification for recipient in derive_active_todos(user, now)]
