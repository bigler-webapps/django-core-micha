"""Source-aware catalogue and direct-workload resolution."""

from dataclasses import dataclass, field
from importlib.util import find_spec
from shutil import which

from django.conf import settings

from .policy import current_policy, policy_enabled, refresh_policy


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


def local_rows(rows, user=None, *, refresh=True):
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
                    effort=None, refresh=True, eligible=None):
    if rows is None:
        from django.apps import apps
        if apps.is_installed("django_core_micha.assistant"):
            from django_core_micha.assistant.catalogue import entries
            rows = entries()
        else:
            rows = ()
    candidates = local_rows(rows, user, refresh=refresh)
    if eligible is not None:
        candidates = [row for row in candidates if eligible(user, row)]
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
    state = current_policy() if policy_enabled() else None
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
    identity = row.get("source_id") or row["driver"]
    return str(row.get("entry_id") or f"{identity}:{row['model']}")


def effective_entry_id(row):
    return _entry_id(row)


ResolvedSelection = ResolvedWorkload
resolve = resolve_workload

