"""Stable family catalogue and private source/model resolution."""

from dataclasses import dataclass, field
from importlib.util import find_spec
from shutil import which

from django.conf import settings

from .policy import (DRIVER_PROVIDERS, PROVIDER_FAMILIES, current_policy,
                     policy_enabled, refresh_policy)


@dataclass(frozen=True)
class ResolvedWorkload:
    row: dict
    source_id: str
    source_label: str
    driver: str
    model: str
    effort: str
    credential: str = field(repr=False)
    billing_source: str
    revision: int | None
    standard: bool
    provider: str = ""
    family: str = ""
    capabilities: tuple = ()


def configured_sources():
    value = getattr(settings, "DCM_AI_POLICY_SOURCES",
                    getattr(settings, "DCM_AI_POLICY_SOURCE_CREDENTIALS", {})) or {}
    return value if isinstance(value, dict) else {}


def source_id(row):
    return str(row.get("source_id") or f"{row.get('driver', '')}:{row.get('model', '')}")


def source_label(row):
    source = source_id(row)
    configured = source_config(source) or {}
    return str(row.get("source_label") or row.get("source_name") or configured.get("label") or source)


def source_config(source):
    value = configured_sources().get(source)
    if isinstance(value, str):
        return {"credential_setting": value}
    return value if isinstance(value, dict) else None


def credential_setting(config):
    return config.get("credential_setting") or config.get("setting")


def source_available(row):
    if not policy_enabled():
        return True
    config = source_config(source_id(row)) or {}
    setting_name = credential_setting(config)
    if not setting_name or not getattr(settings, setting_name, ""):
        return False
    driver = row.get("driver")
    if driver == "claude_cli":
        return bool(which("claude"))
    library = {"openai_api": "openai", "anthropic_api": "anthropic"}.get(driver)
    return library is not None and find_spec(library) is not None


def source_eligible(user, row):
    source = source_id(row)
    config = source_config(source)
    if policy_enabled() and config is None:
        return False
    if config is not None and (config.get("enabled", True) is False or config.get("eligible", True) is False):
        return False
    if config is not None and config.get("driver") and config["driver"] != row.get("driver"):
        return False
    hook = getattr(settings, "DCM_AI_POLICY_SOURCE_ELIGIBLE", None)
    if isinstance(hook, str):
        from django.utils.module_loading import import_string
        hook = import_string(hook)
    if hook:
        try:
            if not hook(user, source):
                return False
        except Exception:
            return False
    return True


def _stable_id(provider, family):
    return f"{provider}:{family}"


def _family_policy_rows(rows, user, state, workload=None):
    templates = []
    for row in rows:
        if isinstance(row.get("provider"), str) and isinstance(row.get("family"), str):
            templates.append(row)
        elif row.get("driver") and row.get("model"):
            identity = family_identity_for_route(row.get("source_id"), row["model"], row["driver"], state=state)
            if identity:
                templates.append({**row, "provider": identity[0], "family": identity[1]})
    # Once an app supplies local catalogue constraints, they must use stable
    # identities. Legacy concrete pins never choose the route in family mode.
    has_constraints = bool(rows)
    result = []
    for choice in state.families or []:
        provider, family = choice["provider"], choice["family"]
        capabilities = tuple(choice["capabilities"])
        if workload is not None and workload not in capabilities:
            continue
        matching = [row for row in templates if row["provider"] == provider and row["family"] == family]
        if has_constraints and len(matching) != 1:
            continue
        template = matching[0] if matching else {}
        local_capabilities = template.get("capabilities")
        if local_capabilities is not None:
            if not isinstance(local_capabilities, (list, tuple, set)):
                continue
            capabilities = tuple(capability for capability in capabilities if capability in local_capabilities)
            if not capabilities or (workload is not None and workload not in capabilities):
                continue
        source = choice["source_id"]
        config = source_config(source) or {}
        driver = config.get("driver")
        if not driver or DRIVER_PROVIDERS.get(driver) != provider:
            continue
        efforts = list(choice.get("efforts") or template.get("efforts") or ["low", "medium", "high", "max"])
        if template.get("efforts"):
            efforts = [effort for effort in efforts if effort in template["efforts"]]
        if not efforts:
            continue
        # The central mapping owns every route field. Local rows can narrow
        # effort/workload settings but cannot pin a source, driver or version.
        row = {key: value for key, value in template.items()
               if key not in {"source_id", "source_label", "driver", "model", "name", "billing", "code_execution"}}
        row.update({"entry_id": _stable_id(provider, family), "provider": provider, "family": family,
                    "family_label": PROVIDER_FAMILIES[provider][family], "name": PROVIDER_FAMILIES[provider][family],
                    "source_id": source, "driver": driver, "model": choice["model_id"],
                    "capabilities": list(capabilities), "efforts": efforts, "billing": "managed",
                    "policy_revision": state.revision})
        if choice.get("code_execution") and "code_execution" in capabilities:
            row["code_execution"] = dict(choice["code_execution"])
        row["source_label"] = source_label(row)
        if source_eligible(user, row) and source_available(row):
            result.append(row)
    return result


def family_identity_for_route(source, model, driver=None, *, state=None):
    """Translate a currently or authoritatively recognized legacy route."""
    state = state or (current_policy() if policy_enabled() else None)
    if not state or not state.families:
        return None
    matches = []
    for choice in state.families:
        current_config = source_config(choice["source_id"]) or {}
        route_matches = ((not source or choice["source_id"] == source) and choice["model_id"] == model and
                         (not driver or current_config.get("driver") == driver))
        legacy_matches = False
        for item in choice.get("legacy", []):
            if (source and item["source_id"] != source) or item["model_id"] != model:
                continue
            old_config = source_config(item["source_id"]) or {}
            old_driver = old_config.get("driver")
            if driver and old_driver and old_driver != driver:
                continue
            resolved_driver = driver or old_driver
            if resolved_driver and DRIVER_PROVIDERS.get(resolved_driver) != choice["provider"]:
                continue
            legacy_matches = True
            break
        if route_matches or legacy_matches:
            matches.append((choice["provider"], choice["family"]))
    return matches[0] if len(matches) == 1 else None


def local_rows(rows, user=None, *, refresh=True, workload=None):
    rows = [dict(row) for row in rows]
    if not policy_enabled():
        return rows
    if refresh:
        try:
            refresh_policy()
        except Exception:
            pass
    state = current_policy()
    if not state:
        return []
    if state.families:
        return _family_policy_rows(rows, user, state, workload)
    allowed = state.allowed or {}
    result = []
    for row in rows:
        source, model = source_id(row), row.get("model")
        if model not in allowed.get(source, ()) or not source_eligible(user, row) or not source_available(row):
            continue
        item = {**row, "source_id": source, "source_label": source_label(row), "policy_revision": state.revision}
        result.append(item)
    return result


def resolve_workload(user, *, rows=None, entry=None, source=None, driver=None, model=None,
                    provider=None, family=None, effort=None, workload=None, refresh=True, eligible=None):
    if rows is None:
        from django.apps import apps
        if apps.is_installed("django_core_micha.assistant"):
            from django_core_micha.assistant.catalogue import entries
            rows = entries()
        else:
            rows = ()
    candidates = local_rows(rows, user, refresh=refresh, workload=workload)
    if eligible is not None:
        candidates = [row for row in candidates if eligible(user, row)]
    state = current_policy() if policy_enabled() else None
    if state and state.families:
        explicit = any(value is not None for value in (entry, source, driver, model, provider, family))
        if provider is not None or family is not None:
            if provider is None or family is None:
                raise ValueError("invalid_selection")
            candidates = [row for row in candidates if row.get("provider") == provider and row.get("family") == family]
        elif entry is not None:
            candidates = [row for row in candidates if _entry_id(row) == entry]
        elif source is not None or driver is not None or model is not None:
            if model is None or (source is None and driver is None):
                raise ValueError("invalid_selection")
            identity = family_identity_for_route(source, model, driver, state=state)
            if identity is None:
                raise ValueError("invalid_selection")
            candidates = [row for row in candidates if (row.get("provider"), row.get("family")) == identity]
        standard = False
        if not explicit:
            candidates = [row for row in candidates if row.get("provider") == state.default_provider and
                          row.get("family") == state.default_family]
            standard = bool(candidates)
        if len(candidates) != 1:
            raise ValueError("invalid_selection" if explicit else "unavailable")
        row = candidates[0]
        selected_effort = effort if effort in (row.get("efforts") or []) else (row.get("efforts") or ["medium"])[0]
        if effort is not None and effort not in (row.get("efforts") or []):
            raise ValueError("invalid_selection")
        config = source_config(source_id(row)) or {}
        setting_name = credential_setting(config)
        credential = getattr(settings, setting_name, "") if isinstance(setting_name, str) else ""
        if not credential:
            raise ValueError("invalid_selection" if explicit else "unavailable")
        if config.get("driver") != row.get("driver") or DRIVER_PROVIDERS.get(config.get("driver")) != row.get("provider"):
            raise ValueError("invalid_selection")
        return ResolvedWorkload(row=row, source_id=source_id(row), source_label=source_label(row),
            driver=row["driver"], model=row["model"], effort=selected_effort, credential=credential,
            billing_source=str(config.get("billing_source") or "managed"), revision=state.revision,
            standard=standard, provider=row["provider"], family=row["family"],
            capabilities=tuple(row.get("capabilities", ())))

    explicit = entry is not None or source is not None or driver is not None or model is not None
    if entry is not None:
        candidates = [row for row in candidates if _entry_id(row) == entry]
    else:
        if source is not None:
            candidates = [row for row in candidates if source_id(row) == source]
        if driver is not None:
            candidates = [row for row in candidates if row.get("driver") == driver]
        if model is not None:
            candidates = [row for row in candidates if row.get("model") == model]
        if explicit and len(candidates) > 1:
            raise ValueError("invalid_selection")
    standard = False
    if not explicit and state:
        candidates = [row for row in candidates if source_id(row) == state.standard_source_id and row.get("model") == state.standard_model_id]
        standard = bool(candidates)
    if not candidates:
        raise ValueError("invalid_selection" if explicit else "unavailable")
    row = candidates[0]
    selected_effort = effort if effort in (row.get("efforts") or []) else (row.get("efforts") or ["medium"])[0]
    if effort is not None and effort not in (row.get("efforts") or []):
        raise ValueError("invalid_selection")
    credential = ""
    billing = "deployment"
    if state:
        config = source_config(source_id(row)) or {}
        if config.get("driver") and config["driver"] != row.get("driver"):
            raise ValueError("invalid_selection")
        setting_name = credential_setting(config)
        credential = getattr(settings, setting_name, "") if isinstance(setting_name, str) else ""
        if not credential:
            raise ValueError("unavailable" if not explicit else "invalid_selection")
        billing = str(config.get("billing_source") or "managed")
    return ResolvedWorkload(row=row, source_id=source_id(row), source_label=source_label(row),
        driver=row["driver"], model=row["model"], effort=selected_effort, credential=credential,
        billing_source=billing, revision=state.revision if state else None, standard=standard)


def _entry_id(row):
    if row.get("provider") and row.get("family"):
        return str(row.get("entry_id") or _stable_id(row["provider"], row["family"]))
    identity = row.get("source_id") or row["driver"]
    return str(row.get("entry_id") or f"{identity}:{row['model']}")


def effective_entry_id(row):
    return _entry_id(row)


ResolvedSelection = ResolvedWorkload
resolve = resolve_workload
