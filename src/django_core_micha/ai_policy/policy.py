"""Validation, durable application and bounded refresh of central AI policy."""

import json
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .transport import EnrollmentTransport

PROVIDER_FAMILIES = {
    "openai": {"astra": "Astra", "sol": "Sol", "terra": "Terra", "luna": "Luna"},
    "claude": {"fable": "Fable", "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"},
}
DRIVER_PROVIDERS = {"openai_api": "openai", "anthropic_api": "claude", "claude_cli": "claude"}
FAMILY_CAPABILITIES = {"chat", "completion", "extraction", "code_execution"}
EFFORTS = {"low", "medium", "high", "max"}


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
    families: list
    verified_execution: list
    default_provider: str
    default_family: str

    def as_dict(self):
        return {"app_id": self.app_id, "environment": self.environment, "revision": self.revision,
                "allowed": self.allowed, "standard": {"source_id": self.standard_source_id,
                "model_id": self.standard_model_id}, "families": self.families, "verified_execution": self.verified_execution,
                "default_family": ({"provider": self.default_provider, "family": self.default_family}
                                   if self.default_provider else None)}


def _validate_families(value, allowed):
    raw = value.get("families", [])
    default = value.get("default_family")
    if raw == [] and default is None:
        return [], "", ""
    if not isinstance(raw, list) or not raw or not isinstance(default, dict):
        raise PolicyError("invalid_policy")
    clean, identities, legacy_routes, assigned_routes = [], set(), set(), set()
    for item in raw:
        if not isinstance(item, dict) or set(item) - {
                "provider", "family", "source_id", "model_id", "capabilities", "efforts", "legacy", "code_execution"}:
            raise PolicyError("invalid_policy")
        provider, family = item.get("provider"), item.get("family")
        source_id, model_id = item.get("source_id"), item.get("model_id")
        capabilities, efforts = item.get("capabilities"), item.get("efforts")
        if (not isinstance(provider, str) or provider not in PROVIDER_FAMILIES or
                not isinstance(family, str) or family not in PROVIDER_FAMILIES[provider] or
                not isinstance(source_id, str) or not source_id.strip() or
                not isinstance(model_id, str) or not model_id.strip() or
                model_id not in allowed.get(source_id, ())):
            raise PolicyError("invalid_policy")
        configured = getattr(settings, "DCM_AI_POLICY_SOURCES",
                             getattr(settings, "DCM_AI_POLICY_SOURCE_CREDENTIALS", {})) or {}
        source_config = configured.get(source_id)
        if isinstance(source_config, str):
            source_config = None
        if (not isinstance(source_config, dict) or
                DRIVER_PROVIDERS.get(source_config.get("driver")) != provider or
                source_config.get("enabled", True) is False or
                source_config.get("eligible", True) is False):
            raise PolicyError("invalid_policy")
        identity = (provider, family)
        if identity in identities:
            raise PolicyError("invalid_policy")
        identities.add(identity)
        current_route = (source_id, model_id)
        if current_route in assigned_routes:
            raise PolicyError("invalid_policy")
        assigned_routes.add(current_route)
        if (not isinstance(capabilities, list) or not capabilities or
                any(not isinstance(capability, str) or capability not in FAMILY_CAPABILITIES for capability in capabilities) or
                len(set(capabilities)) != len(capabilities)):
            raise PolicyError("invalid_policy")
        if efforts is not None and (not isinstance(efforts, list) or not efforts or
                any(not isinstance(effort, str) or effort not in EFFORTS for effort in efforts) or
                len(set(efforts)) != len(efforts)):
            raise PolicyError("invalid_policy")
        legacy = item.get("legacy", [])
        if not isinstance(legacy, list):
            raise PolicyError("invalid_policy")
        clean_legacy = []
        for route in legacy:
            if not isinstance(route, dict) or set(route) != {"source_id", "model_id"}:
                raise PolicyError("invalid_policy")
            old_source, old_model = route.get("source_id"), route.get("model_id")
            if (not isinstance(old_source, str) or not old_source.strip() or
                    not isinstance(old_model, str) or not old_model.strip()):
                raise PolicyError("invalid_policy")
            old_pair = (old_source, old_model)
            if old_pair in legacy_routes or old_pair in assigned_routes:
                raise PolicyError("invalid_policy")
            legacy_routes.add(old_pair)
            assigned_routes.add(old_pair)
            clean_legacy.append({"source_id": old_source, "model_id": old_model})
        code_execution = item.get("code_execution")
        if "code_execution" in capabilities:
            if not isinstance(code_execution, dict) or code_execution.get("enabled") is not True:
                raise PolicyError("invalid_policy")
            if source_config.get("driver") not in {"openai_api", "anthropic_api"}:
                raise PolicyError("invalid_policy")
            allowed_execution_keys = {"enabled", "tool_version", "memory_limit", "max_execution_seconds",
                                      "max_continuations", "usd_micros", "unit"}
            if set(code_execution) - allowed_execution_keys:
                raise PolicyError("invalid_policy")
            expected_tool = "code_interpreter" if provider == "openai" else "code_execution_20250825"
            expected_unit = "openai_container_20m" if provider == "openai" else "anthropic_execution_5m"
            if (code_execution.get("tool_version") != expected_tool or
                    code_execution.get("memory_limit", "1g") not in {"1g", "4g", "16g", "64g"} or
                    type(code_execution.get("usd_micros")) is not int or code_execution["usd_micros"] <= 0 or
                    code_execution.get("unit") != expected_unit):
                raise PolicyError("invalid_policy")
            for key, ceiling in (("max_execution_seconds", 3600), ("max_continuations", 32)):
                if key in code_execution and (type(code_execution[key]) is not int or not 0 < code_execution[key] <= ceiling):
                    raise PolicyError("invalid_policy")
        elif code_execution is not None:
            raise PolicyError("invalid_policy")
        clean_item = {"provider": provider, "family": family, "source_id": source_id,
                      "model_id": model_id, "capabilities": list(capabilities), "legacy": clean_legacy}
        if efforts is not None:
            clean_item["efforts"] = list(efforts)
        if code_execution is not None:
            clean_item["code_execution"] = dict(code_execution)
        clean.append(clean_item)
    default_provider, default_family = default.get("provider"), default.get("family")
    if (set(default) != {"provider", "family"} or
            (default_provider, default_family) not in identities or
            not any(row["provider"] == default_provider and row["family"] == default_family and
                    "chat" in row["capabilities"] for row in clean)):
        raise PolicyError("invalid_policy")
    return clean, default_provider, default_family


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
    families, default_provider, default_family = _validate_families(value, clean)
    verification = value.get("verified_execution", [])
    if not isinstance(verification, list):
        raise PolicyError("invalid_policy")
    seen = set()
    for proof in verification:
        if not isinstance(proof, dict) or set(proof) != {"source_id", "model_id", "driver", "tool_version", "evidence"}:
            raise PolicyError("invalid_policy")
        if any(not isinstance(proof[key], str) or not proof[key].strip() for key in proof):
            raise PolicyError("invalid_policy")
        key = (proof["source_id"], proof["model_id"], proof["driver"], proof["tool_version"])
        configured = getattr(settings, "DCM_AI_POLICY_SOURCES", {}) or {}
        config = configured.get(proof["source_id"], {}) if isinstance(configured, dict) else {}
        if (key in seen or not isinstance(config, dict) or config.get("driver") != proof["driver"] or
                proof["model_id"] not in clean.get(proof["source_id"], ()) or
                proof["tool_version"] != {"openai_api": "code_interpreter", "anthropic_api": "code_execution_20250825"}.get(proof["driver"])):
            raise PolicyError("invalid_policy")
        seen.add(key)
    return ValidatedPolicy(app_id, environment, revision, clean, source_id, model_id,
                           families, [dict(item) for item in verification], default_provider, default_family)


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
                            "standard_model_id": checked.standard_model_id,
                            "families": checked.families, "verified_execution": checked.verified_execution, "default_provider": checked.default_provider,
                            "default_family": checked.default_family},
        )
        if not created:
            if current.app_id != checked.app_id or current.environment != checked.environment:
                raise PolicyError("policy_target_mismatch")
            if checked.revision < current.revision:
                return False
            if checked.revision == current.revision:
                old = {"app_id": current.app_id, "environment": current.environment, "revision": current.revision,
                       "allowed": current.allowed, "standard": {"source_id": current.standard_source_id,
                       "model_id": current.standard_model_id}, "families": current.families, "verified_execution": current.verified_execution,
                       "default_family": ({"provider": current.default_provider, "family": current.default_family}
                                          if current.default_provider else None)}
                if old != checked.as_dict():
                    raise PolicyError("conflicting_revision")
                return False
            current.app_id, current.environment, current.revision = checked.app_id, checked.environment, checked.revision
            current.allowed = checked.allowed
            current.standard_source_id, current.standard_model_id = checked.standard_source_id, checked.standard_model_id
            current.families = checked.families
            current.verified_execution = checked.verified_execution
            current.default_provider, current.default_family = checked.default_provider, checked.default_family
            current.applied_at = timezone.now()
            current.save(update_fields=("app_id", "environment", "revision", "allowed", "standard_source_id",
                                        "standard_model_id", "families", "verified_execution", "default_provider", "default_family", "applied_at"))
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
            "model_id": state.standard_model_id}, "families": state.families, "verified_execution": state.verified_execution,
            "default_family": ({"provider": state.default_provider, "family": state.default_family}
                               if state.default_provider else None)}


validate_revision = validate_policy
apply_enrollment = apply_policy
update_policy = apply_policy
