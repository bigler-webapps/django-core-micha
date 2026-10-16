"""Strict provider schema: every property is required and objects reject extras."""
from copy import deepcopy
from django.conf import settings
from django.utils.module_loading import import_string
from django_core_micha.extraction import validate_schema

CORE = {"merchant":{"type":"string"},"date":{"type":"string"},"total":{"type":"string"},"currency":{"type":"string"},"payment_method":{"type":"string","enum":["cash","card","other","unknown"]},"items":{"type":"array","items":{"type":"object","properties":{"description":{"type":"string"},"amount":{"type":"string"},"box":{"type":"object","properties":{"x":{"type":"number"},"y":{"type":"number"},"w":{"type":"number"},"h":{"type":"number"}},"required":["x","y","w","h"],"additionalProperties":False}},"required":["description","amount","box"],"additionalProperties":False}},"vat":{"type":"array","items":{"type":"object","properties":{"rate":{"type":"string"},"amount":{"type":"string"}},"required":["rate","amount"],"additionalProperties":False}},"box":{"type":"object","properties":{"x":{"type":"number"},"y":{"type":"number"},"w":{"type":"number"},"h":{"type":"number"}},"required":["x","y","w","h"],"additionalProperties":False},"confidence":{"type":"string","enum":["high","low"]}}

def resolve_extension(extension=None):
    if extension is not None: return extension
    path=getattr(settings,"DCM_RECEIPTS_EXTENSION",None)
    return import_string(path) if path else None

def core_schema(with_accounts=False): return build_schema(None,with_accounts)

def build_schema(extension=None, with_accounts=False):
    ext=resolve_extension(extension); props=deepcopy(CORE)
    if with_accounts:
        props["account_suggestions"]={"type":"array","items":{"type":"object","properties":{"account_id":{"type":"string"},"confidence":{"type":"string","enum":["high","low"]}},"required":["account_id","confidence"],"additionalProperties":False}}
    extra=(getattr(ext,"properties",{}) or {}) if ext else {}
    if set(extra)&set(props): raise ValueError("Receipt extension cannot replace core properties")
    props.update(deepcopy(extra)); schema={"type":"object","properties":{"receipts":{"type":"array","items":{"type":"object","properties":props,"required":list(props),"additionalProperties":False}}},"required":["receipts"],"additionalProperties":False}
    validate_schema(schema); return schema

