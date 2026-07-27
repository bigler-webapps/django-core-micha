"""Materialize live todo providers into canonical notification status rows."""
from dataclasses import replace

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from ..models import Notification, NotificationRecipient
from .engine import TODO_TIMEZONE, materialize_todo, resolve_due_date
from .models import TodoOverride
from .registry import get_todo_config, get_todo_provider, iter_registered_todo_types


def _get_override(scope, type_key: str):
    if scope is None:
        return None
    return TodoOverride.objects.filter(
        content_type=ContentType.objects.get_for_model(scope),
        object_id=str(scope.pk),
        type_key=type_key,
    ).first()


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


def sync_todos_for_user(user, now=None) -> list[Notification]:
    """Materialize all currently visible provider todos for one user.

    ``Notification.content`` always carries the freshly materialized payload (raw
    provider content plus the live ``due``/``severity``) so every canonical consumer
    (``feed/``, dispatchers) sees current values without knowing about the todo
    channel. ``get_or_create_by_dedup`` only applies ``content`` on first creation, so
    an existing row is explicitly re-synced below when its stored content has gone
    stale.
    """

    resolved_now = now or timezone.now()
    active: dict[int, Notification] = {}
    for type_key in iter_registered_todo_types():
        provider_fn = get_todo_provider(type_key)
        config = get_todo_config(type_key)
        for seed in provider_fn(user, resolved_now):
            if seed.type_key != type_key:
                raise ValueError(f"Todo provider {type_key!r} emitted seed for {seed.type_key!r}")
            scope = seed.scope if seed.scope is not None else seed.notifiable
            override = _get_override(scope, seed.type_key)
            if override is not None and not override.enabled:
                continue
            effective_engine_config = _engine_config(_apply_lead_override(config, override))
            due_base_resolver = seed.due_base_resolver or (lambda base: None)
            materialized = materialize_todo(
                seed.content,
                effective_engine_config,
                resolved_now,
                due_base_resolver=due_base_resolver,
                has_due_time=seed.has_due_time,
                tz=TODO_TIMEZONE,
            )
            if materialized is None:
                continue
            notification, created = Notification.objects.get_or_create_by_dedup(
                notification_type=seed.type_key,
                category="todo",
                notifiable=seed.notifiable,
                content=materialized,
                urgency="normal",
            )
            if not created and notification.content != materialized:
                notification.content = materialized
                notification.save(update_fields=["content"])
            recipient, _ = NotificationRecipient.objects.get_or_create(
                notification=notification,
                user=seed.recipient,
            )
            if recipient.dismissed_at is not None or recipient.done_at is not None:
                continue
            notification._todo_due_at = resolve_due_date(
                effective_engine_config["due"], due_base_resolver, TODO_TIMEZONE
            )
            active[notification.pk] = notification
    return sorted(active.values(), key=lambda notification: notification.created_at, reverse=True)
