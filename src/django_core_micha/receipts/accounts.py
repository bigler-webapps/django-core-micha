"""Resolve app accounts and constrain model suggestions to that list."""
import logging
from django.conf import settings
from django.utils.module_loading import import_string
log=logging.getLogger(__name__)

def get_accounts(user,context=None):
    path=getattr(settings,"DCM_RECEIPTS_ACCOUNTS_HOOK",None)
    if not path:return None
    out=[]
    try:
        hook=import_string(path) if isinstance(path,str) else path
        for row in hook(user,context) or []:  # iterated inside the guard: a lazy hook may fail while yielding
            if not isinstance(row,dict):continue
            ident=" ".join(str(row.get("id") or "").split()); name=" ".join(str(row.get("name") or "").split())[:100]  # one line each: names go into the prompt
            if ident and name:out.append({"id":ident,"name":name})
            if len(out)>=200:break
    except Exception as exc:log.warning("Receipt accounts hook failed (%s)",type(exc).__name__);return None
    return out or None

def filter_suggestions(suggestions,accounts):
    if not accounts:return []
    allowed={a["id"] for a in accounts};seen=set();out=[]
    for row in suggestions or []:
        if not isinstance(row,dict):continue
        ident=str(row.get("account_id") or "")
        if ident in allowed and ident not in seen:
            out.append({"account_id":ident,"confidence":"high" if row.get("confidence")=="high" else "low"});seen.add(ident)
        if len(out)==3:break
    return out

