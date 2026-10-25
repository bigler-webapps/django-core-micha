"""App-owned model catalogue and startup consistency check."""
from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from .availability import availability
from .hooks import _hook

DRIVERS = {"claude_cli", "openai_api", "anthropic_api"}
EFFORTS = {"low", "medium", "high", "max"}

def entries():
    rows = getattr(settings, "DCM_ASSISTANT_CATALOGUE", ()) or ()
    return [dict(row) for row in rows]

DEFAULT_LEVELS = {"claude-fable-5-1": "top", "gpt-6-astra": "top", "claude-opus-5-5": "best",
                  "claude-sonnet-5-5": "balanced", "gpt-6.1-sol": "balanced",
                  "claude-haiku-5-5": "fast", "gpt-6-luna": "fast"}

def model_level(row):
    value = row.get("level")
    if value is None:
        value = (getattr(settings, "DCM_ASSISTANT_MODEL_LEVELS", {}) or {}).get(row["model"],
                 DEFAULT_LEVELS.get(row["model"], "balanced"))
    return value if value in {"top", "best", "balanced", "fast"} else "balanced"

def entry_usable(user, row):
    if not entry_enabled(row):
        return False
    if availability().get("providers", {}).get(row["driver"]) != "available":
        return False
    try:
        fn = _hook("ENTRY_ALLOWED")
        return bool(fn(user, row)) if fn else True
    except Exception:
        return False

def usable_entries(user):
    return [row for row in entries() if entry_usable(user, row)]

def default_entry(user=None):
    rows = usable_entries(user) if user is not None else entries()
    if user is not None and rows:
        from .limit import stored_default_entry
        selected = stored_default_entry()
        if selected:
            preferred = next((r for r in rows if entry_id(r) == selected), None)
            if preferred:
                return preferred
    level = getattr(settings, "DCM_ASSISTANT_DEFAULT_LEVEL", "balanced")
    return next((row for row in rows if model_level(row) == level),
                next((row for row in rows if row.get("default")), rows[0] if rows else None))

def entry_id(row):
    return f"{row['driver']}:{row['model']}"

def selection_of(row, effort=None):
    efforts = row.get("efforts") or ["medium"]
    return {"entry": entry_id(row), "effort": effort if effort in efforts else efforts[0]}

def entry_by_id(value):
    return next((row for row in entries() if entry_id(row) == value), None)

def entry_enabled(row):
    from .limit import disabled_entry_ids
    return entry_id(row) not in disabled_entry_ids()

def validate(driver, model, effort, user=None):
    for row in entries():
        if row.get("driver") == driver and row.get("model") == model and effort in row.get("efforts", []) and (user is None or entry_usable(user, row)):
            return row
    raise ValueError("invalid_selection")

def validate_catalogue():
    rows = entries()
    if not rows: return
    defaults = 0
    keys = set()
    for row in rows:
        if row.get("driver") not in DRIVERS or not isinstance(row.get("model"), str) or not row.get("model"):
            raise ImproperlyConfigured("Invalid DCM_ASSISTANT_CATALOGUE driver or model")
        key = (row["driver"], row["model"])
        if key in keys: raise ImproperlyConfigured("Duplicate DCM_ASSISTANT_CATALOGUE entry")
        keys.add(key)
        if row.get("default") is True: defaults += 1
        if not row.get("efforts") or not set(row["efforts"]) <= EFFORTS:
            raise ImproperlyConfigured("Invalid DCM_ASSISTANT_CATALOGUE effort list")
        if row.get("billing") not in {"subscription", "pay_per_use"}:
            raise ImproperlyConfigured("Invalid DCM_ASSISTANT_CATALOGUE billing tag")
    if defaults != 1: raise ImproperlyConfigured("DCM_ASSISTANT_CATALOGUE must have exactly one default")

@register()
def catalogue_check(app_configs, **kwargs):
    errors = []
    try: validate_catalogue()
    except ImproperlyConfigured as exc: return [Error(str(exc), id="dcm_assistant.E000")]
    from .limit import limit_setting_errors
    errors.extend(Error(message, id="dcm_assistant.E002") for message in
                  limit_setting_errors(getattr(settings, "DCM_ASSISTANT_COST_LIMIT", None)))
    from django_core_micha.extraction.cost_guard import _get_cost_table
    rates = _get_cost_table()
    for row in entries():
        if row.get("billing") == "pay_per_use" and row["model"] not in rates:
            errors.append(Error(f"Pay-per-use assistant model {row['model']} has no AI cost rate", id="dcm_assistant.E001"))
    return errors
