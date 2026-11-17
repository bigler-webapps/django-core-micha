"""Validation, durable application and bounded refresh of central AI policy."""

import json
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .transport import EnrollmentTransport


class PolicyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ValidatedPolicy:
    app_id: str
    environment: str
    revision: int
    allowed: dict
    standard_source_id: str
    standard_model_id: str

    def as_dict(self):
        return {"app_id": self.app_id, "environment": self.environment, "revision": self.revision,
                "allowed": self.allowed, "standard": {"source_id": self.standard_source_id,
                "model_id": self.standard_model_id}}


def _payload(value):
    if not isinstance(value, dict):
        raise PolicyError("invalid_policy")
    # Central deployments may wrap the policy with the credential-derived target.
    target = value.get("target")
    policy = value.get("policy", value)
    if not isinstance(policy, dict):
        raise PolicyError("invalid_policy")
    if isinstance(target, dict):
        policy = {**policy, "app_id": target.get("app_id", policy.get("app_id")),
                  "environment": target.get("environment", policy.get("environment"))}
    return policy


def validate_policy(value, *, expected_app_id=None, expected_environment=None):
    value = _payload(value)
    app_id, environment = value.get("app_id"), value.get("environment")
    revision, allowed, standard = value.get("revision"), value.get("allowed"), value.get("standard")
    if not isinstance(app_id, str) or not app_id.strip() or not isinstance(environment, str) or not environment.strip():
        raise PolicyError("invalid_policy")
    if expected_app_id and app_id != expected_app_id or expected_environment and environment != expected_environment:
        raise PolicyError("policy_target_mismatch")
    if type(revision) is not int or revision <= 0 or not isinstance(allowed, dict) or not allowed:
        raise PolicyError("invalid_policy")
    clean = {}
    for source_id, models in allowed.items():
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(models, list) or not models:
            raise PolicyError("invalid_policy")
        if any(not isinstance(model_id, str) or not model_id.strip() for model_id in models) or len(set(models)) != len(models):
            raise PolicyError("invalid_policy")
        clean[source_id] = list(models)
    if not isinstance(standard, dict):
        raise PolicyError("invalid_policy")
    source_id, model_id = standard.get("source_id"), standard.get("model_id")
    if not isinstance(source_id, str) or not source_id.strip() or not isinstance(model_id, str) or not model_id.strip():
        raise PolicyError("invalid_policy")
    if model_id not in clean.get(source_id, ()):
        raise PolicyError("invalid_policy")
    return ValidatedPolicy(app_id, environment, revision, clean, source_id, model_id)


def policy_enabled():
    return bool(getattr(settings, "DCM_AI_POLICY_ENABLED", False) or
                (getattr(settings, "DCM_AI_POLICY_BASE_URL", "") and
                 getattr(settings, "DCM_AI_POLICY_ENROLLMENT_CREDENTIAL", "")))


def current_policy():
    if not policy_enabled():
        return None
    from .models import AIModelPolicy

    state = AIModelPolicy.objects.first()
    if not state:
        return None
    expected_app = getattr(settings, "DCM_AI_POLICY_APP_ID", "") or None
    expected_environment = getattr(settings, "DCM_AI_POLICY_ENVIRONMENT", "") or None
    if expected_app and state.app_id != expected_app or expected_environment and state.environment != expected_environment:
        return None
    return state


def apply_policy(value):
    from .models import AIModelPolicy

    app_id = getattr(settings, "DCM_AI_POLICY_APP_ID", "")
    environment = getattr(settings, "DCM_AI_POLICY_ENVIRONMENT", "")
    if not app_id or not environment:
        raise PolicyError("policy_not_configured")
    checked = validate_policy(value, expected_app_id=app_id, expected_environment=environment)
    with transaction.atomic():
        current, created = AIModelPolicy.objects.select_for_update().get_or_create(
            pk=1, defaults={"app_id": checked.app_id, "environment": checked.environment,
                            "revision": checked.revision, "allowed": checked.allowed,
                            "standard_source_id": checked.standard_source_id,
                            "standard_model_id": checked.standard_model_id},
        )
        if not created:
            if current.app_id != checked.app_id or current.environment != checked.environment:
                raise PolicyError("policy_target_mismatch")
            if checked.revision < current.revision:
                return False
            if checked.revision == current.revision:
                old = {"app_id": current.app_id, "environment": current.environment, "revision": current.revision,
                       "allowed": current.allowed, "standard": {"source_id": current.standard_source_id,
                       "model_id": current.standard_model_id}}
                if old != checked.as_dict():
                    raise PolicyError("conflicting_revision")
                return False
            current.app_id, current.environment, current.revision = checked.app_id, checked.environment, checked.revision
            current.allowed = checked.allowed
            current.standard_source_id, current.standard_model_id = checked.standard_source_id, checked.standard_model_id
            current.applied_at = timezone.now()
            current.save(update_fields=("app_id", "environment", "revision", "allowed", "standard_source_id",
                                        "standard_model_id", "applied_at"))
        return True


def refresh_policy(*, transport=None, force=False):
    if not policy_enabled():
        return None
    from .models import AIModelPolicy

    existing = current_policy()
    interval = max(1, int(getattr(settings, "DCM_AI_POLICY_REFRESH_INTERVAL", 10)))
    if existing and not force and existing.last_refresh_at and (timezone.now() - existing.last_refresh_at).total_seconds() < interval:
        return existing
    transport = transport or EnrollmentTransport()
    payload = transport.get()
    apply_policy(payload)
    state = AIModelPolicy.objects.get(pk=1)
    AIModelPolicy.objects.filter(pk=1).update(last_refresh_at=timezone.now())
    try:
        # Acknowledgement is sent only after the durable apply/read succeeds.
        transport.applied(state.revision)
    except Exception:
        # The applied state remains authoritative; the next bounded refresh retries the ack.
        pass
    return AIModelPolicy.objects.get(pk=1)


def acknowledge_revision(revision, *, transport=None):
    from .models import AIModelPolicy

    state = AIModelPolicy.objects.get(pk=1)
    if state.revision != revision:
        raise PolicyError("unapplied_revision")
    return (transport or EnrollmentTransport()).applied(revision)


def policy_dict(state=None):
    state = state or current_policy()
    if not state:
        return None
    return {"app_id": state.app_id, "environment": state.environment, "revision": state.revision,
            "allowed": state.allowed, "standard": {"source_id": state.standard_source_id,
            "model_id": state.standard_model_id}}


validate_revision = validate_policy
apply_enrollment = apply_policy
update_policy = apply_policy
