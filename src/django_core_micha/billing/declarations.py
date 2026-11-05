"""Durable cancellation and withdrawal declaration intake."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from django_core_micha.auth.client_ip import resolve_client_ip

from .models import Contract, Declaration

log=logging.getLogger(__name__)

CONFIRM_SALT="dcm-billing-declaration-confirm-v1"


class DeclarationInputError(ValueError):
    def __init__(self,code): self.code=code


def _required_text(data,key,maximum,required=False):
    value=data.get(key,"")
    if not isinstance(value,str): raise DeclarationInputError("invalid_"+key)
    value=value.strip()
    if (required and not value) or len(value)>maximum: raise DeclarationInputError("invalid_"+key)
    return value


def validate_declaration(data):
    if not isinstance(data,dict): raise DeclarationInputError("invalid_request")
    kind=data.get("kind")
    if kind not in ("cancellation","withdrawal"): raise DeclarationInputError("invalid_kind")
    name=_required_text(data,"name",200,required=True)
    email=_required_text(data,"email",254,required=True)
    try: validate_email(email)
    except Exception: raise DeclarationInputError("invalid_email")
    hint=_required_text(data,"contract_hint",200)
    cancellation_type=data.get("cancellation_type","")
    if kind=="cancellation":
        if cancellation_type not in ("ordinary","extraordinary"): raise DeclarationInputError("invalid_cancellation_type")
    else: cancellation_type=""
    requested=data.get("requested_end_date")
    if requested in (None,""): requested_date=None
    elif isinstance(requested,str) and len(requested)<=10:
        requested_date=parse_date(requested)
        if requested_date is None: raise DeclarationInputError("invalid_requested_end_date")
    else: raise DeclarationInputError("invalid_requested_end_date")
    reason=_required_text(data,"reason",2000,required=kind=="cancellation" and cancellation_type=="extraordinary")
    if kind=="cancellation" and cancellation_type!="extraordinary": reason=""
    message=_required_text(data,"message",2000)
    return {"kind":kind,"name":name,"email":email,"contract_hint":hint,"cancellation_type":cancellation_type,
            "requested_end_date":requested_date,"reason":reason,"message":message}


def _throttle(bucket,identity,limit):
    digest=hashlib.sha256(identity.encode("utf-8","replace")).hexdigest()
    key=f"dcm-billing-declaration:{bucket}:{digest}"
    if cache.add(key,1,timeout=3600): return True
    try: count=cache.incr(key)
    except ValueError:
        cache.set(key,2,timeout=3600); count=2
    return count<=limit


def check_public_throttles(request,email=""):
    address=resolve_client_ip(request) or "unknown"
    if not _throttle("ip",address,10): return False
    if email and not _throttle("email",email.strip().lower(),3): return False
    return True


def _confirmation_token(declaration):
    nonce=secrets.token_urlsafe(24)
    value=signing.dumps({"reference":declaration.reference,"nonce":nonce},salt=CONFIRM_SALT)
    declaration.confirm_token_hash=hashlib.sha256(value.encode()).hexdigest()
    declaration.confirm_expires_at=declaration.received_at+timedelta(days=14)
    return value


def create_public_declaration(values):
    """Store a public declaration and answer. Everything that depends on whether the address belongs to a contract
    (the match, the owner link, the confirmation token and mail) runs after the response in `finish_public_declaration`,
    so a matching and a non-matching submission do the same work before the response."""
    app=getattr(settings,"DCM_BILLING_APP","")
    with transaction.atomic():
        declaration=Declaration.objects.create(app=app,kind=values["kind"],channel="public",name=values["name"],email=values["email"],
            contract_hint=values["contract_hint"],cancellation_type=values["cancellation_type"],requested_end_date=values["requested_end_date"],
            reason=values["reason"],message=values["message"],status="received")
        transaction.on_commit(lambda pk=declaration.pk: _in_background(finish_public_declaration,pk))
    return declaration


def finish_public_declaration(declaration_id):
    """Match a stored public declaration to a contract, then send the owner confirmation and the acknowledgement.

    Only a declaration still `received` is processed, under a row lock, so the background thread and
    `billing_process_declarations` cannot both finish it."""
    app=getattr(settings,"DCM_BILLING_APP","")
    token=""
    with transaction.atomic():
        declaration=Declaration.objects.select_for_update().filter(pk=declaration_id,app=app,channel="public",status="received").first()
        if declaration is None: return False
        matches=list(Contract.objects.filter(app=app,user__email__iexact=declaration.email,status__in=("active","cancellation_scheduled")).select_related("user")[:2])
        confirm_url=getattr(settings,"DCM_BILLING_DECLARATION_CONFIRM_URL","")
        if len(matches)==1 and confirm_url and "{token}" in confirm_url:
            contract=matches[0]; declaration.contract=contract; declaration.user=contract.user
            declaration.status="awaiting_confirmation"; token=_confirmation_token(declaration)
            declaration.save(update_fields=("contract","user","status","confirm_token_hash","confirm_expires_at"))
        else:
            declaration.status="needs_review"; declaration.save(update_fields=("status",))
    if token: send_confirmation_email(declaration.pk,token)
    send_acknowledgement(declaration.pk)
    return True


def _in_background(function,*args):
    """Send public-intake mail off the request thread, so the response time cannot tell a matching address from another.
    Failures stay `failed` and `billing_process_declarations` retries them."""
    def run():
        try: function(*args)
        except Exception as exc: log.error("Declaration mail failed (%s)",type(exc).__name__)
        finally: connection.close()
    threading.Thread(target=run,daemon=True).start()


def _receipt_times(received):
    utc=received.astimezone(dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    zurich=timezone.localtime(received,timezone=__import__("zoneinfo").ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d %H:%M %Z")
    return utc,zurich


def send_acknowledgement(declaration_id):
    declaration=Declaration.objects.filter(pk=declaration_id).first()
    if not declaration or declaration.ack_email_status=="sent" or not declaration.email: return False
    utc,zurich=_receipt_times(declaration.received_at)
    req=f"Requested end date: {declaration.requested_end_date.isoformat()}.\n" if declaration.requested_end_date else ""
    kind_de={"cancellation":"Kündigung","withdrawal":"Widerruf"}.get(declaration.kind,declaration.kind)
    type_de={"ordinary":"ordentlich","extraordinary":"ausserordentlich"}.get(declaration.cancellation_type,"")
    req_de=f"Gewünschtes Vertragsende: {declaration.requested_end_date.isoformat()}.\n" if declaration.requested_end_date else ""
    text=(f"Ihre Erklärung ist eingegangen: {kind_de}{' (' + type_de + ')' if type_de else ''}.\nReferenz: {declaration.reference}\n"
        f"Name: {declaration.name}\nE-Mail: {declaration.email}\nVertragsangabe: {declaration.contract_hint}\n{req_de}"
        f"Begründung: {declaration.reason}\nNachricht: {declaration.message}\nEingegangen: {utc} / {zurich}.\n"
        "Das Datum, zu dem die Erklärung wirksam wird, bestätigen wir separat.\n\n---\n\n"
        f"Your declaration has been received: {declaration.kind}{' (' + declaration.cancellation_type + ')' if declaration.cancellation_type else ''}.\n"
        f"Reference: {declaration.reference}\nName: {declaration.name}\nEmail: {declaration.email}\nContract reference or hint: {declaration.contract_hint}\n"
        f"{req}Reason: {declaration.reason}\nMessage: {declaration.message}\nReceived: {utc} / {zurich}.\nThe effective date will be confirmed separately.")
    accepted=False
    try:
        accepted=send_mail("Eingangsbestätigung / Declaration receipt",text,getattr(settings,"DEFAULT_FROM_EMAIL",None),[declaration.email],fail_silently=False)>0
    except Exception:
        accepted=False
    declaration.ack_attempts+=1
    declaration.ack_email_status="sent" if accepted else "failed"
    declaration.ack_sent_at=timezone.now() if accepted else None
    declaration.save(update_fields=("ack_attempts","ack_email_status","ack_sent_at"))
    return accepted


def send_confirmation_email(declaration_id,token):
    declaration=Declaration.objects.filter(pk=declaration_id).select_related("user").first()
    if not declaration or not declaration.user_id or declaration.confirm_email_status=="sent": return False
    template=getattr(settings,"DCM_BILLING_DECLARATION_CONFIRM_URL","")
    if not template or "{token}" not in template: return False
    link=template.replace("{token}",token)
    accepted=False
    try:
        accepted=send_mail("Erklärung bestätigen / Confirm your declaration",f"Bitte bestätigen Sie Ihre Erklärung über diesen Link:\n{link}\n\nConfirm your declaration by opening this link:\n{link}",
            getattr(settings,"DEFAULT_FROM_EMAIL",None),[declaration.user.email],fail_silently=False)>0
    except Exception:
        accepted=False
    declaration.confirm_attempts+=1; declaration.confirm_email_status="sent" if accepted else "failed"
    declaration.confirm_sent_at=timezone.now() if accepted else None
    declaration.save(update_fields=("confirm_attempts","confirm_email_status","confirm_sent_at"))
    return accepted


def retry_declaration_emails():
    app=getattr(settings,"DCM_BILLING_APP","")
    # A background thread that never ran (restart between commit and thread start) leaves the declaration `received`.
    for row in Declaration.objects.filter(app=app,channel="public",status="received",received_at__lt=timezone.now()-timedelta(minutes=5)):
        finish_public_declaration(row.pk)
    pending=Declaration.objects.filter(app=app,ack_email_status__in=("pending","failed"))
    for row in pending: send_acknowledgement(row.pk)
    for row in Declaration.objects.filter(app=app,confirm_email_status__in=("pending","failed"),status="awaiting_confirmation",confirm_expires_at__gt=timezone.now()):
        token=_confirmation_token(row)
        row.save(update_fields=("confirm_token_hash","confirm_expires_at"))
        send_confirmation_email(row.pk,token)
    for row in Declaration.objects.filter(app=app,confirm_email_status__in=("pending","failed"),status="awaiting_confirmation",confirm_expires_at__lte=timezone.now()):
        row.status="needs_review"; row.decision_note="confirmation_expired_before_delivery"; row.save(update_fields=("status","decision_note"))


def create_owner_declaration(contract,kind,*,cancellation_type="",requested_end_date=None,reason="",message=""):
    with transaction.atomic():
        contract=Contract.objects.select_for_update().select_related("user","purchase").get(pk=contract.pk)
        if kind=="withdrawal":
            prior=Declaration.objects.filter(contract=contract,kind=kind,channel="account",status__in=("received","accepted","scheduled","effective")).order_by("-received_at","-pk").first()
            if prior: return prior
        declaration=Declaration.objects.create(app=contract.app,kind=kind,channel="account",name=contract.user.get_full_name()[:200],
            email=contract.user.email[:254],contract_hint=contract.reference,cancellation_type=cancellation_type,
            requested_end_date=requested_end_date,reason=reason,message=message,user=contract.user,contract=contract,status="received")
    from .contracts import apply_cancellation,apply_withdrawal
    if kind=="cancellation": return apply_cancellation(declaration)
    return apply_withdrawal_declaration(declaration)


def apply_withdrawal_declaration(declaration):
    contract=declaration.contract
    if contract.withdrawal_policy=="full":
        from .contracts import apply_withdrawal
        return apply_withdrawal(declaration)
    from .withdrawal import withdraw
    try:
        withdraw(contract.purchase,now=declaration.received_at)
        contract.status="withdrawn"; contract.save(update_fields=("status","updated_at"))
        declaration.status="effective"; declaration.effective_at=declaration.received_at
    except ValueError as exc:
        declaration.status="needs_review"; declaration.decision_note=str(exc)[:2000]
        contract.needs_review=True; contract.review_reason=str(exc)[:120]; contract.save(update_fields=("needs_review","review_reason","updated_at"))
    declaration.save(update_fields=("status","effective_at","decision_note"))
    return declaration


def confirm_public_declaration(token):
    try:
        data=signing.loads(token,salt=CONFIRM_SALT,max_age=14*24*60*60)
        ref=data.get("reference")
    except Exception:
        return None
    token_hash=hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        declaration=Declaration.objects.select_for_update().filter(reference=ref,channel="public").select_related("contract").first()
        if not declaration or declaration.status!="awaiting_confirmation" or not declaration.confirm_token_hash or declaration.confirmed_at or not declaration.confirm_expires_at or declaration.confirm_expires_at<=timezone.now(): return None
        if not secrets.compare_digest(declaration.confirm_token_hash,token_hash) or not declaration.contract_id: return None
        declaration.confirmed_at=timezone.now(); declaration.confirm_token_hash=""; declaration.user=declaration.contract.user
        declaration.save(update_fields=("confirmed_at","confirm_token_hash","user"))
        from .contracts import apply_cancellation,apply_withdrawal
        if declaration.kind=="cancellation": apply_cancellation(declaration)
        else: apply_withdrawal_declaration(declaration)
        return declaration

