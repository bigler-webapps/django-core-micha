from __future__ import annotations

from typing import Callable, NamedTuple

from django.core.exceptions import ImproperlyConfigured


class RegistryEntry(NamedTuple):
    redact_fields: frozenset[str]
    context_resolver: Callable | None


_REGISTRY: dict[str, RegistryEntry] = {}


def register(
    model,
    redact_fields: frozenset[str] | set[str] = frozenset(),
    context_resolver: Callable | None = None,
) -> None:
    """Register a model for audit logging.

    model: a Django Model class or "<app_label>.<ModelName>" string.
    redact_fields: field attnames whose values are replaced with "***" before DB write.
    context_resolver: optional callable(instance) -> any, stored in metadata["context"].
        Must NOT return PII — the context key is not covered by redact_fields (S3).
    """
    from django.apps import apps

    if isinstance(model, str):
        app_label, model_name = model.split(".")
        model_class = apps.get_model(app_label, model_name)
    else:
        model_class = model

    label = model_class._meta.label_lower

    if label in _REGISTRY:
        raise ImproperlyConfigured(
            f"auditlog: {label!r} is already registered. "
            "Each model may only be registered once."
        )

    _REGISTRY[label] = RegistryEntry(
        redact_fields=frozenset(redact_fields),
        context_resolver=context_resolver,
    )


def get_registry() -> dict[str, RegistryEntry]:
    return _REGISTRY
