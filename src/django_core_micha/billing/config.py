from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qsl

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
    continuation: str | None = None
    available: bool = True

@dataclass(frozen=True)
class BillingConfig:
    app: str
    currency: str
    plans: tuple[Plan, ...]
    token_rates: dict
    contract: dict | None = None

_FIELDS = {"subscription": {"key","kind","name","amount_cents","interval","token_allowance","tax_code","continuation","available"}, "continuation": {"key","kind","name","amount_cents","interval","token_allowance","tax_code"}, "team_package": {"key","kind","name","amount_cents","term_months","seats","token_allowance","tax_code","available"}, "token_package": {"key","kind","name","amount_cents","tokens","tax_code","available"}}
def load_billing_config():
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("Install django-core-micha[billing] to load billing.yaml") from exc
    base_dir = getattr(settings, "BASE_DIR", Path.cwd())
    path = Path(getattr(settings, "DCM_BILLING_FILE", Path(base_dir) / "billing.yaml"))
    with path.open("r", encoding="utf-8") as f: raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or set(raw) - {"app","currency","plans","token_rates","contract"} or not {"app","currency","plans","token_rates"} <= set(raw): raise ValueError("billing.yaml must contain app, currency, plans, token_rates, and optional contract")
    app = getattr(settings, "DCM_BILLING_APP", "")
    if not app or raw["app"] != app: raise ValueError("billing.yaml app must match DCM_BILLING_APP")
    if raw["currency"] != "usd": raise ValueError("only usd currency is supported")
    if not isinstance(raw["plans"], list) or not raw["plans"]: raise ValueError("plans must be a non-empty list")
    plans, keys = [], set()
    for item in raw["plans"]:
        if not isinstance(item, dict) or item.get("kind") not in _FIELDS: raise ValueError("plan has invalid kind or shape")
        kind = item["kind"]
        optional={"available"}
        if kind in ("subscription","team_package"): optional.add("token_allowance")
        if kind=="subscription": optional.add("continuation")
        required = _FIELDS[kind] - optional
        if set(item) - _FIELDS[kind] or not required <= set(item): raise ValueError(f"{kind} plan has unknown or missing fields")
        key = item["key"]
        if not isinstance(key,str) or not re.fullmatch(r"[a-z0-9_]{1,40}",key) or key in keys: raise ValueError("plan keys must be unique [a-z0-9_]{1,40}")
        keys.add(key)
        if not isinstance(item["name"],str) or not item["name"] or not isinstance(item["tax_code"],str) or not item["tax_code"]: raise ValueError("plan name and tax_code are required")
        for field in ("amount_cents", "term_months", "seats", "tokens", "token_allowance"):
            if field in item and (type(item[field]) is not int or item[field] <= 0): raise ValueError(f"{field} must be a positive integer")
        if kind in ("subscription","continuation") and item["interval"] not in ("month","year"): raise ValueError("subscription interval must be month or year")
        if kind == "continuation" and item["interval"] != "month": raise ValueError("continuation plan interval must be month")
        if kind == "subscription" and item.get("continuation") and item["interval"] != "year": raise ValueError("continuation is only valid on an annual subscription")
        if "available" in item and type(item["available"]) is not bool: raise ValueError("available must be a boolean")
        if "continuation" in item and (not isinstance(item["continuation"],str) or not re.fullmatch(r"[a-z0-9_]{1,40}",item["continuation"])): raise ValueError("continuation must name a plan")
        plans.append(Plan(**item))
    by_key={p.key:p for p in plans}
    referenced=set()
    for plan in plans:
        if plan.continuation:
            target=by_key.get(plan.continuation)
            if not target or target.kind != "continuation": raise ValueError("continuation must reference a continuation plan")
            referenced.add(target.key)
    if any(p.kind == "continuation" and p.key not in referenced for p in plans): raise ValueError("unreferenced continuation plan")
    rates = raw["token_rates"]
    if not isinstance(rates,dict) or any(not isinstance(k,str) or type(v) is not int or v <= 0 for k,v in rates.items()): raise ValueError("token_rates must map names to positive integers")
    contract=raw.get("contract")
    if contract is not None:
        if not isinstance(contract,dict) or set(contract)-{"terms_version","seller","withdrawal_refund","public_declarations","transition_guard_days"}: raise ValueError("contract must be a mapping with supported policy fields")
        if not isinstance(contract.get("terms_version"),str) or not contract["terms_version"].strip() or len(contract["terms_version"])>80: raise ValueError("contract.terms_version is required and must be at most 80 characters")
        if not isinstance(contract.get("seller"),str) or not contract["seller"].strip() or len(contract["seller"])>255: raise ValueError("contract.seller is required and must be at most 255 characters")
        if contract.get("withdrawal_refund","prorated") not in ("full","prorated"): raise ValueError("contract.withdrawal_refund must be full or prorated")
        if type(contract.get("public_declarations",False)) is not bool: raise ValueError("contract.public_declarations must be a boolean")
        guard=contract.get("transition_guard_days",7)
        if type(guard) is not int or not 1 <= guard <= 60: raise ValueError("contract.transition_guard_days must be 1..60")
        contract={"terms_version":contract["terms_version"],"seller":contract["seller"],"withdrawal_refund":contract.get("withdrawal_refund","prorated"),"public_declarations":contract.get("public_declarations",False),"transition_guard_days":guard}
    return BillingConfig(app,"usd",tuple(plans),rates,contract)

def _hook(setting):
    value = getattr(settings,setting,None)
    return import_string(value) if isinstance(value,str) else value

def load_checkout_contexts():
    """Return validated contextual destinations and configuration errors."""
    raw=getattr(settings,"DCM_BILLING_CHECKOUT_CONTEXTS",None)
    if raw is None or raw == {}: return {}, []
    errors=[]; result={}
    if not isinstance(raw,dict): return {}, ["DCM_BILLING_CHECKOUT_CONTEXTS must be a mapping."]
    for key,value in raw.items():
        prefix=f"Context {key} "
        if not isinstance(key,str) or not re.fullmatch(r"[a-z0-9_]{1,40}",key,flags=re.ASCII):
            errors.append("Context keys must match [a-z0-9_]{1,40}."); continue
        if not isinstance(value,dict) or set(value)!={"success_url","cancel_url"}:
            errors.append(f"{prefix}must contain exactly success_url and cancel_url."); continue
        valid_entry=True
        for field in ("success_url","cancel_url"):
            url=value[field]
            try:
                parsed=urlsplit(url) if isinstance(url,str) else None
                valid=bool(parsed and parsed.scheme in ("http","https") and parsed.hostname and not any(k=="checkout_attempt" for k,_ in parse_qsl(parsed.query,keep_blank_values=True)))
            except ValueError: valid=False
            if not valid: errors.append(f"{prefix}{field} must be an absolute HTTP(S) URL without checkout_attempt."); valid_entry=False
        if valid_entry: result[key]=value.copy()
    return (result if not errors else {}), errors
