from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from dataclasses import dataclass
from pathlib import Path
import re

@dataclass(frozen=True)
class Plan:
    key: str
    kind: str
    name: str
    amount_cents: int
    interval: str | None = None
    term_months: int | None = None
    seats: int | None = None
    tokens: int | None = None
    token_allowance: int = 0
    tax_code: str = ""

@dataclass(frozen=True)
class BillingConfig:
    app: str
    currency: str
    plans: tuple[Plan, ...]
    token_rates: dict

_FIELDS = {"subscription": {"key","kind","name","amount_cents","interval","token_allowance","tax_code"}, "team_package": {"key","kind","name","amount_cents","term_months","seats","token_allowance","tax_code"}, "token_package": {"key","kind","name","amount_cents","tokens","tax_code"}}
def load_billing_config():
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("Install django-core-micha[billing] to load billing.yaml") from exc
    base_dir = getattr(settings, "BASE_DIR", Path.cwd())
    path = Path(getattr(settings, "DCM_BILLING_FILE", Path(base_dir) / "billing.yaml"))
    with path.open("r", encoding="utf-8") as f: raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or set(raw) != {"app","currency","plans","token_rates"}: raise ValueError("billing.yaml must contain exactly app, currency, plans, token_rates")
    app = getattr(settings, "DCM_BILLING_APP", "")
    if not app or raw["app"] != app: raise ValueError("billing.yaml app must match DCM_BILLING_APP")
    if raw["currency"] != "usd": raise ValueError("only usd currency is supported")
    if not isinstance(raw["plans"], list) or not raw["plans"]: raise ValueError("plans must be a non-empty list")
    plans, keys = [], set()
    for item in raw["plans"]:
        if not isinstance(item, dict) or item.get("kind") not in _FIELDS: raise ValueError("plan has invalid kind or shape")
        kind = item["kind"]
        required = _FIELDS[kind] - ({"token_allowance"} if kind in ("subscription", "team_package") else set())
        if set(item) - _FIELDS[kind] or not required <= set(item): raise ValueError(f"{kind} plan has unknown or missing fields")
        key = item["key"]
        if not isinstance(key,str) or not re.fullmatch(r"[a-z0-9_]{1,40}",key) or key in keys: raise ValueError("plan keys must be unique [a-z0-9_]{1,40}")
        keys.add(key)
        if not isinstance(item["name"],str) or not item["name"] or not isinstance(item["tax_code"],str) or not item["tax_code"]: raise ValueError("plan name and tax_code are required")
        for field in ("amount_cents", "term_months", "seats", "tokens", "token_allowance"):
            if field in item and (type(item[field]) is not int or item[field] <= 0): raise ValueError(f"{field} must be a positive integer")
        if kind == "subscription" and item["interval"] not in ("month","year"): raise ValueError("subscription interval must be month or year")
        plans.append(Plan(**item))
    rates = raw["token_rates"]
    if not isinstance(rates,dict) or any(not isinstance(k,str) or type(v) is not int or v <= 0 for k,v in rates.items()): raise ValueError("token_rates must map names to positive integers")
    return BillingConfig(app,"usd",tuple(plans),rates)

def _hook(setting):
    value = getattr(settings,setting,None)
    return import_string(value) if isinstance(value,str) else value
