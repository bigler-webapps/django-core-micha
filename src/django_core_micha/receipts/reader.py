"""Consent-gated receipt recognition using the shared extraction provider."""
import logging
from copy import deepcopy
from django.conf import settings
from django_core_micha.extraction import ExtractionRequest, extract_document, require_json_payload
from django_core_micha.extraction.cost_guard import AICostLimitExceeded
from .accounts import get_accounts,filter_suggestions
from .consent import has_consent
from .errors import ReceiptError
from .limits import check_and_increment
from .normalise import normalise_receipts
from .prepare import prepare
from .prompt import build_prompts
from .schema import CORE, build_schema,resolve_extension
log=logging.getLogger(__name__)

def read_receipts(user,content:bytes,*,context=None,extension=None):
    if not getattr(user,"is_authenticated",False):raise ReceiptError("authentication_required")
    if not has_consent(user):raise ReceiptError("consent_required")
    if not content:raise ReceiptError("invalid_request")
    from .prepare import MAX_FILE_BYTES,sniff_type
    if len(content)>MAX_FILE_BYTES:raise ReceiptError("file_too_large")
    sniff_type(content)
    check_and_increment(user)
    ext=resolve_extension(extension);accounts=get_accounts(user,context)
    try:
        prepared=prepare(content)
        mode=getattr(settings,"DCM_RECEIPTS_READER","live")
        if mode not in ("live","fake"):raise RuntimeError("unknown DCM_RECEIPTS_READER")  # a typo must never fall through to the live provider
        if mode=="fake":
            from .fake import read
            payload=read(content)
        else:
            if not has_consent(user):raise ReceiptError("consent_required")  # narrows the window for a revocation that arrived while the file was prepared
            system,user_prompt=build_prompts(ext,accounts)
            req=ExtractionRequest(system_prompt=system,user_prompt=user_prompt,schema=build_schema(ext,bool(accounts)),max_output_tokens=int(getattr(settings,"DCM_RECEIPTS_MAX_OUTPUT_TOKENS",4000)),effort=getattr(settings,"DCM_RECEIPTS_EFFORT","low"),thinking={},image_bytes=prepared.image_bytes,image_mime_type=prepared.image_mime_type,pdf_text=prepared.pdf_text,upload_bytes=prepared.upload_bytes,upload_mime_type=prepared.upload_mime_type,upload_filename=prepared.upload_filename)
            provider=getattr(settings,"DCM_RECEIPTS_PROVIDER","openai");model=getattr(settings,"DCM_RECEIPTS_MODEL","gpt-6-luna")
            key=getattr(settings,"OPENAI_API_KEY" if provider=="openai" else "ANTHROPIC_API_KEY","")
            result=extract_document(req,provider=provider,api_key=key,model=model,user_id=user.pk)
            payload=require_json_payload(result.raw_text,getattr(result,"raw_response",None))
    except ReceiptError:raise
    except AICostLimitExceeded as exc:raise ReceiptError("spend_cap_reached") from exc
    except Exception as exc:
        log.warning("Receipt provider failed (%s)",type(exc).__name__)
        raise ReceiptError("provider_unavailable") from exc
    try:rows=normalise_receipts(payload.get("receipts",[]) if isinstance(payload,dict) else [])
    except Exception as exc:
        log.warning("Receipt payload normalisation failed (%s)",type(exc).__name__)
        raise ReceiptError("provider_unavailable") from exc
    out=[];allowed=set(CORE)|{"account_suggestions"}|set((getattr(ext,"properties",None) or {}) if ext else {})
    for row in rows:
        for stray in [k for k in row if k not in allowed]:row.pop(stray)  # nothing the schema did not ask for reaches the app
        if ext and callable(getattr(ext,"normalise",None)):
            core=deepcopy({k:row[k] for k in row if k in CORE})
            try:changed=ext.normalise(deepcopy(row),context)
            except Exception as exc:log.warning("Receipt extension normaliser failed (%s)",type(exc).__name__);changed=row
            for k in row:
                if k not in core and k in changed:row[k]=changed[k]
            row.update(core)
        if accounts:row["account_suggestions"]=filter_suggestions(row.get("account_suggestions",[]),accounts)
        else:row.pop("account_suggestions",None)  # no hook, no suggestion, whatever the provider returned
        out.append(row)
    if not out:raise ReceiptError("nothing_recognised")
    return out

