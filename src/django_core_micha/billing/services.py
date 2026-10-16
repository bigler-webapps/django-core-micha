from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import logging
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone
from .config import load_billing_config
from .models import TokenAccount, TokenEntry, Subscription, TeamSeat, FreeAccess, TeamPackage, AICostRecord

log=logging.getLogger(__name__)
class InsufficientTokens(Exception): pass
@dataclass(frozen=True)
class AccessState:
    status: str
    source: str|None
    ends_at: object|None

def access_state(user):
    now=timezone.now(); candidates=[]; ends=[]
    for s in Subscription.objects.filter(user=user):
        end=s.current_period_end
        if end and s.status in ("active","trialing","past_due") and end>now: candidates.append((end,"subscription"))
        elif s.ended_at: ends.append(s.ended_at)
        elif end and end<=now and s.status in ("active","trialing","past_due","canceled","unpaid","incomplete_expired"): ends.append(end)
    for seat in TeamSeat.objects.filter(user=user).select_related("package"):
        p=seat.package
        if seat.active and p.starts_at<=now<p.ends_at: candidates.append((p.ends_at,"team"))
        elif not seat.active and seat.removed_at: ends.append(seat.removed_at)
        elif seat.active and p.ends_at<=now: ends.append(p.ends_at)
        elif not seat.active: ends.append(p.ends_at if p.ends_at<=now else now)
    free=FreeAccess.objects.filter(user=user,revoked_at__isnull=True).order_by("-granted_at").first()
    if free: return AccessState("active","free",None)
    revoked=FreeAccess.objects.filter(user=user,revoked_at__isnull=False).order_by("-revoked_at").values_list("revoked_at",flat=True).first()
    if revoked: ends.append(revoked)
    if candidates:
        end,source=max(candidates,key=lambda row:row[0]); return AccessState("active",source,end)
    ended=max(ends,default=None)
    # Operator decision 2026-10-16: nothing is deleted automatically. Without access the app stays read-only, without a date.
    return AccessState("read_only",None,ended)

def _account(owner, create=True):
    from .models import TeamPackage
    query={"team_package":owner} if isinstance(owner,TeamPackage) else {"user":owner}
    if create: return TokenAccount.objects.get_or_create(**query)[0]
    return TokenAccount.objects.filter(**query).first()
def token_balance(owner):
    account=_account(owner,False)
    if not account: return 0
    now=timezone.now()
    credits=account.entries.filter(kind__in=("grant","purchase","adjustment"),remaining__gt=0).filter(models_q_expiry(now)).aggregate(v=Sum("remaining"))["v"] or 0
    return int(credits)

def models_q_expiry(now):
    from django.db.models import Q
    return Q(expires_at__isnull=True)|Q(expires_at__gt=now)

def grant_tokens(owner,amount,*,expires_at=None,reference,purpose):
    if type(amount) is not int or amount<=0: raise ValueError("amount must be positive")
    account=_account(owner)
    with transaction.atomic():
        if reference:
            row,_=TokenEntry.objects.get_or_create(account=account,kind="grant",reference=reference,defaults={"amount":amount,"remaining":amount,"expires_at":expires_at,"purpose":purpose})
            return row
        return TokenEntry.objects.create(account=account,kind="grant",amount=amount,remaining=amount,expires_at=expires_at,reference="",purpose=purpose)
def credit_purchase(owner,amount,*,reference):
    if type(amount) is not int or amount<=0: raise ValueError("amount must be positive")
    if not reference: raise ValueError("a purchase needs a reference")
    account=_account(owner)
    with transaction.atomic():
        row,created=TokenEntry.objects.get_or_create(account=account,kind="purchase",reference=reference,defaults={"amount":amount,"remaining":amount})
        return row

def debit_tokens(owner,amount,*,purpose,reference):
    if type(amount) is not int or amount<=0: raise ValueError("amount must be positive")
    if not reference: raise ValueError("a debit needs a reference")
    account=_account(owner)
    with transaction.atomic():
        account=TokenAccount.objects.select_for_update().get(pk=account.pk)
        old=TokenEntry.objects.filter(account=account,kind="debit",reference=reference).first()
        if old:
            if old.amount!=-amount: raise ValueError("debit_reference_reused")  # a retry must repeat the same debit
            return old
        now=timezone.now()
        from django.db.models import F
        credits=list(TokenEntry.objects.select_for_update().filter(account=account,kind__in=("grant","purchase","adjustment"),remaining__gt=0).filter(models_q_expiry(now)).order_by(F("expires_at").asc(nulls_last=True),"created_at","pk"))
        if sum(x.remaining for x in credits)<amount: raise InsufficientTokens("insufficient_tokens")
        left=amount
        for grant in credits:
            take=min(left,grant.remaining); grant.remaining-=take; grant.save(update_fields=("remaining",)); left-=take
            if not left: break
        return TokenEntry.objects.create(account=account,kind="debit",amount=-amount,purpose=purpose,reference=reference,app=getattr(settings,"DCM_BILLING_APP",""))

def reverse_debit(owner, *, reference, reason):
    if not reason: raise ValueError("reason_required")
    account=_account(owner,False)
    debit=TokenEntry.objects.filter(account=account,kind="debit",reference=reference).first() if account else None
    if debit is None: raise ValueError("unknown_debit")
    reversal_ref=f"reversal:{reference}"
    with transaction.atomic():
        account=TokenAccount.objects.select_for_update().get(pk=account.pk)
        row,created=TokenEntry.objects.get_or_create(account=account,kind="adjustment",reference=reversal_ref,defaults={"amount":abs(int(debit.amount)),"remaining":abs(int(debit.amount)),"purpose":str(reason)[:120],"app":getattr(settings,"DCM_BILLING_APP","")})
        return row
def expire_tokens(now=None):
    now=now or timezone.now(); count=0
    with transaction.atomic():
        grants=TokenEntry.objects.select_for_update().filter(kind="grant",expires_at__lte=now,remaining__gt=0)
        for g in grants:
            amt=g.remaining; g.remaining=0; g.save(update_fields=("remaining",))
            TokenEntry.objects.get_or_create(account=g.account,kind="expiry",reference=f"expiry:{g.pk}",defaults={"amount":-amt,"purpose":"expired grant"}); count+=1
    return count
def tokens_for(rate_name,units):
    if type(units) is not int or units<0: raise ValueError("units must be a non-negative integer")
    rates=load_billing_config().token_rates
    if rate_name not in rates: raise KeyError(rate_name)
    return units*rates[rate_name]

def record_ai_call(*,user,provider,model,input_tokens,output_tokens,cache_tokens=0,cache_read_tokens=0,cache_write_tokens=0,purpose,reference=""):
    try:
        from django_core_micha.ai_prices import price_usage
        read_tokens = int(cache_read_tokens or 0) + (int(cache_tokens or 0) if not cache_read_tokens and not cache_write_tokens else 0)
        write_tokens = int(cache_write_tokens or 0)
        micros = price_usage(model, input_tokens=input_tokens, output_tokens=output_tokens,
                             cache_read_tokens=read_tokens, cache_write_tokens=write_tokens)
        with transaction.atomic():
            return AICostRecord.objects.create(app=getattr(settings,"DCM_BILLING_APP",""),user=user,provider=provider,model=model,input_tokens=max(0,int(input_tokens)),output_tokens=max(0,int(output_tokens)),cache_tokens=max(0,read_tokens+write_tokens),cost_usd_micros=max(0,micros),purpose=purpose,reference=reference)
    except Exception:
        log.error("Could not record AI cost (%s)", __import__("sys").exc_info()[0].__name__)
        return None
