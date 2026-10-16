"""Pure receipt field normalisers."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

def amount(value):
    if value is None:return ""
    s=str(value).strip().replace("CHF","").replace("chf","").replace("'","").replace(" ","")
    if not s:return ""
    if "," in s and "." in s:
        s=s.replace(".","").replace(",",".") if s.rfind(",")>s.rfind(".") else s.replace(",","")
    elif "," in s:s=s.replace(",",".")
    try:
        n=abs(Decimal(s).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))
        return "" if n==0 else format(n,"f")
    except (InvalidOperation,ValueError):return ""

def currency(value, default="CHF"):
    s=str(value or "").strip().upper(); return s if re.fullmatch(r"[A-Z]{3}",s,flags=re.ASCII) else default

def date(value):
    s=str(value or "").strip()
    for fmt in ("%Y-%m-%d","%d.%m.%Y"):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    return ""

def payment_method(value):
    s=str(value or "").strip().lower()
    if s in {"cash","bar","bargeld","cash payment"}:return "cash"
    if s in {"card","credit card","debit card","karte","ec"}:return "card"
    if s in {"other","bank transfer","twint","invoice"}:return "other"
    return "unknown"

def box(value):
    if not isinstance(value,dict):return None
    try:b={k:max(0.0,min(1.0,float(value.get(k,0) or 0))) for k in ("x","y","w","h")}
    except (TypeError,ValueError):return None
    return None if b["w"]==0 or b["h"]==0 else b

def _text(v,cap):return str(v or "").strip()[:cap]

def normalise_receipt(r, default_currency="CHF"):
    out=dict(r or {}); out["merchant"]=_text(out.get("merchant"),200); out["date"]=date(out.get("date")); out["total"]=amount(out.get("total")); out["currency"]=currency(out.get("currency"),default_currency); out["payment_method"]=payment_method(out.get("payment_method")); out["box"]=box(out.get("box")); out["confidence"]="high" if out.get("confidence")=="high" else "low"
    items=[]
    for i in (out.get("items") or [])[:100]:
        if isinstance(i,dict): items.append({"description":_text(i.get("description"),200),"amount":amount(i.get("amount")),"box":box(i.get("box"))})
    out["items"]=items; vats=[]
    for v in (out.get("vat") or [])[:20]:
        if isinstance(v,dict):vats.append({"rate":amount(v.get("rate")),"amount":amount(v.get("amount"))})
    out["vat"]=vats
    return out if out["merchant"] or out["total"] else None

def normalise_receipts(rows, default_currency="CHF"):
    return [r for v in (rows or [])[:50] if (r:=normalise_receipt(v,default_currency)) is not None]

