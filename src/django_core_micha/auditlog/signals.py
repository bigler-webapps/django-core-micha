import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save

from .audit_context import get_current_actor_id, get_current_request_id
from .registry import get_registry
from .services.redact import REDACTED_PLACEHOLDER, redact_metadata
from .services.serialization import serialize

logger = logging.getLogger(__name__)

DIFF_IGNORED_FIELDS = {"updated_at"}

_SIGNALS_CONNECTED = False


def _instance_state(instance) -> dict:
    return {
        field.attname: serialize(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
    }


def _resolve_actor_id(instance):
    # ContextVar set by AuditlogActorMiddleware is authoritative (S1).
    # Field fallback is a best-effort convenience: apps must ensure those FK
    # fields are never set from user-controlled input without server-side validation.
    actor_id = get_current_actor_id()
    if actor_id:
        return actor_id
    for attr in ("updated_by_id", "created_by_id", "requested_by_id", "uploaded_by_id"):
        val = getattr(instance, attr, None)
        if val:
            return val
    return None


def _calculate_changes(before: dict, after: dict) -> dict:
    changes = {}
    for key, new_value in after.items():
        if key in DIFF_IGNORED_FIELDS:
            continue
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {"from": old_value, "to": new_value}
    return changes


def _build_metadata(instance, action: str, changes: dict, before: dict, after: dict, entry) -> dict:
    metadata = {
        "model": instance._meta.label_lower,
        "object_id": str(instance.pk),
        "action": action,
        "changes": changes,
        "before": before,
        "after": after,
    }
    request_id = get_current_request_id()
    if request_id:
        metadata["request_id"] = request_id
    if entry.context_resolver is not None:
        try:
            metadata["context"] = entry.context_resolver(instance)
        except Exception:
            logger.warning(
                "auditlog: context_resolver raised for %s pk=%s",
                instance._meta.label_lower,
                instance.pk,
                exc_info=True,
            )
            metadata["context"] = None
    return metadata


def _create_audit_event(instance, action: str, changes: dict, before: dict, after: dict, entry):
    from .models import AuditEvent

    metadata = _build_metadata(instance, action, changes, before, after, entry)
    if entry.redact_fields:
        redact_metadata(metadata, entry.redact_fields)

    try:
        with transaction.atomic():
            AuditEvent.objects.create(
                actor_id=_resolve_actor_id(instance),
                event_type=f"{instance._meta.label_lower}.{action}",
                event_code=f"auditlog.{action}",
                message=f"{instance._meta.object_name} {action}",
                metadata=metadata,
            )
    except Exception:
        # Never let an audit write failure abort the application save (R2).
        logger.exception(
            "auditlog: failed to write AuditEvent for %s.%s pk=%s",
            instance._meta.label_lower,
            action,
            instance.pk,
        )


def _capture_previous_state(sender, instance, **kwargs):
    if instance._state.adding or not instance.pk:
        return
    if get_registry().get(sender._meta.label_lower) is None:
        return
    previous = sender.objects.filter(pk=instance.pk).first()
    if previous is None:
        return
    instance._audit_previous_state = _instance_state(previous)


def _log_model_save(sender, instance, created, **kwargs):
    entry = get_registry().get(sender._meta.label_lower)
    if entry is None:
        return
    after = _instance_state(instance)
    if created:
        before = {}
        changes = {
            k: {"from": None, "to": v}
            for k, v in after.items()
            if k not in DIFF_IGNORED_FIELDS
        }
        _create_audit_event(instance, "created", changes, before, after, entry)
        return

    before = getattr(instance, "_audit_previous_state", {}) or {}
    changes = _calculate_changes(before, after)
    if not changes:
        return
    _create_audit_event(instance, "updated", changes, before, after, entry)


def _log_model_delete(sender, instance, **kwargs):
    entry = get_registry().get(sender._meta.label_lower)
    if entry is None:
        return
    before = _instance_state(instance)
    changes = {
        k: {"from": v, "to": None}
        for k, v in before.items()
        if k not in DIFF_IGNORED_FIELDS
    }
    _create_audit_event(instance, "deleted", changes, before, {}, entry)


def connect_signals():
    global _SIGNALS_CONNECTED
    if _SIGNALS_CONNECTED:
        return
    registry = get_registry()
    from django.apps import apps

    for label in registry:
        app_label, model_name = label.split(".")
        try:
            model_class = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        uid = f"auditlog_{label}"
        pre_save.connect(_capture_previous_state, sender=model_class, dispatch_uid=f"{uid}_pre_save")
        post_save.connect(_log_model_save, sender=model_class, dispatch_uid=f"{uid}_post_save")
        post_delete.connect(_log_model_delete, sender=model_class, dispatch_uid=f"{uid}_post_delete")

    _SIGNALS_CONNECTED = True
