"""EU/EEA consumer withdrawal rules and execution."""
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Purchase, Subscription, TeamPackage, TokenEntry

log = logging.getLogger(__name__)
EU_EEA_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS LI NO".split())

@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str = "eligible"

@dataclass(frozen=True)
class RefundCalculation:
    time_share_cents: int
    token_share_cents: int
    used_cents: int
    refund_cents: int

def eligibility(purchase, now):
    if not getattr(settings, "DCM_BILLING_WITHDRAWAL_ENABLED", True): return Eligibility(False, "withdrawal_disabled")
    if purchase.status != "active": return Eligibility(False, "not_active")
    if purchase.billing_country not in EU_EEA_COUNTRIES: return Eligibility(False, "country_not_eligible")
    if purchase.has_business_tax_id: return Eligibility(False, "business_purchase")
    if now - purchase.purchased_at > timedelta(days=14): return Eligibility(False, "period_over")
    return Eligibility(True)

def _owner_accounts(purchase):
    from .models import TokenAccount
    if purchase.kind == "team_package":
        return TokenAccount.objects.filter(team_package__stripe_ref=purchase.stripe_object_id)
    return TokenAccount.objects.filter(user=purchase.user)

def _clean(value):
    return " ".join(str(value or "").split())[:200]

def _ceil_div(n, d): return (n + d - 1) // d if n else 0

def refund_amount(purchase, now):
    amount = int(purchase.amount_paid_cents)
    time_share = 0
    if purchase.kind != "token_package" and purchase.tokens_granted == 0 and purchase.term_start and purchase.term_end:
        seconds = max(0, (purchase.term_end - purchase.term_start).total_seconds())
        term_days = max(1, ceil(seconds / 86400))
        used_days = min(term_days, max(0, ceil(max(0, (now - purchase.term_start).total_seconds()) / 86400)))
        time_share = _ceil_div(used_days * amount, term_days)
    elif purchase.kind != "token_package" and purchase.term_start and purchase.term_end:
        seconds = max(0, (purchase.term_end - purchase.term_start).total_seconds())
        term_days = max(1, ceil(seconds / 86400))
        used_days = min(term_days, max(0, ceil(max(0, (now - purchase.term_start).total_seconds()) / 86400)))
        time_share = _ceil_div(used_days * amount, term_days)
    entries = TokenEntry.objects.filter(reference__in=purchase.grant_references, kind__in=("grant", "purchase"),
                                        account__in=_owner_accounts(purchase))
    granted = int(purchase.tokens_granted or 0)
    consumed = 0
    for entry in entries:
        expired = -TokenEntry.objects.filter(account=entry.account, kind="expiry", reference=f"expiry:{entry.pk}").values_list("amount", flat=True).first() if TokenEntry.objects.filter(account=entry.account, kind="expiry", reference=f"expiry:{entry.pk}").exists() else 0
        consumed += max(0, int(entry.amount) - int(entry.remaining or 0) - max(0, expired))
    token_share = _ceil_div(min(granted, consumed) * amount, granted) if granted else 0
    used = min(amount, max(time_share, token_share))
    return RefundCalculation(time_share, token_share, used, max(0, amount - used))

def _note(calc, currency):
    return f"Time share: {calc.time_share_cents} {currency} cents; token share: {calc.token_share_cents} {currency} cents; used: {calc.used_cents} {currency} cents; refund: {calc.refund_cents} {currency} cents."

def _send_confirmation(purchase, now, calc, note):
    if Purchase.objects.filter(pk=purchase.pk, withdrawal_email_sent=False).update(withdrawal_email_sent=True) != 1: return
    try:
        from django.core.mail import send_mail
        from django_core_micha.emails import get_preferred_language, get_greeting_name
        lang = get_preferred_language(purchase.user)
        date = timezone.localtime(now).strftime("%Y-%m-%d %H:%M %Z")
        greeting = _clean(get_greeting_name(purchase.user))
        plan_key = _clean(purchase.plan_key)
        if lang.startswith("de"):
            subject = "Bestätigung Ihres Widerrufs"
            body = f"Guten Tag {greeting},\n\nIhr Widerruf für {plan_key} wurde am {date} erfasst. Bezahlt: {purchase.amount_paid_cents} {purchase.currency}. {note} Die Rückzahlung erfolgt innerhalb von 14 Tagen auf das ursprüngliche Zahlungsmittel."
        else:
            subject = "Withdrawal confirmation"
            body = f"Hello {greeting},\n\nYour withdrawal for {plan_key} was recorded on {date}. Paid: {purchase.amount_paid_cents} {purchase.currency}. {note} The refund will be paid to the original payment method within 14 days."
        send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), [purchase.user.email])
    except Exception as exc:
        log.error("Withdrawal confirmation email failed (%s)", type(exc).__name__)

def _send_after_commit(pk, now, calc, note):
    purchase = Purchase.objects.select_related("user").filter(pk=pk).first()
    if purchase:
        _send_confirmation(purchase, now, calc, note)

def withdraw(purchase, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        purchase = Purchase.objects.select_for_update().select_related("user").get(pk=purchase.pk)
        if purchase.status == "withdrawn":
            calc = RefundCalculation(0, 0, purchase.amount_paid_cents - (purchase.refund_cents or 0), purchase.refund_cents or 0)
            return calc, True
        eligible = eligibility(purchase, now)
        if not eligible.eligible: raise ValueError(eligible.reason)
        # lock the owner's accounts so a concurrent debit cannot make the token share stale
        list(_owner_accounts(purchase).select_for_update().values_list("pk", flat=True))
        calc = refund_amount(purchase, now)
        if calc.refund_cents and not purchase.stripe_payment_intent_id: raise ValueError("payment_reference_missing")
        if purchase.kind == "subscription":
            sub = Subscription.objects.select_for_update().filter(stripe_subscription_id=purchase.stripe_object_id).first()
            try:
                from .stripe_service import _stripe
                _stripe().Subscription.cancel(purchase.stripe_object_id)
            except Exception as exc:
                # Stripe can report that the subscription was already canceled by a prior attempt.
                if "already canceled" not in str(exc).lower() and getattr(exc, "http_status", None) != 404: raise
            if sub:
                sub.status="canceled"; sub.ended_at=now; sub.current_period_end=now; sub.cancel_at_period_end=False
                sub.save(update_fields=("status","ended_at","current_period_end","cancel_at_period_end"))
        elif purchase.kind == "team_package":
            TeamPackage.objects.filter(stripe_ref=purchase.stripe_object_id).update(ends_at=now)
        for entry in TokenEntry.objects.select_for_update().filter(reference__in=purchase.grant_references, kind__in=("grant","purchase"), remaining__gt=0, account__in=_owner_accounts(purchase)):
            left = int(entry.remaining or 0)
            if left:
                entry.remaining = 0; entry.save(update_fields=("remaining",))
                TokenEntry.objects.get_or_create(account=entry.account, kind="expiry", reference=f"withdrawal:{purchase.pk}:{entry.pk}", defaults={"amount":-left,"purpose":"withdrawal","app":getattr(settings,"DCM_BILLING_APP","")})
        refund_id = ""
        if calc.refund_cents:
            from .stripe_service import _stripe
            refund = _stripe().Refund.create(payment_intent=purchase.stripe_payment_intent_id, amount=calc.refund_cents,
                metadata={"app":getattr(settings,"DCM_BILLING_APP",""),"purchase_id":str(purchase.pk)}, reason="requested_by_customer",
                idempotency_key=f"dcm-withdrawal:{getattr(settings,'DCM_BILLING_APP','')}:{purchase.pk}")
            refund_id = getattr(refund,"id","") or (refund.get("id","") if isinstance(refund,dict) else "")
        note = _note(calc, purchase.currency)
        purchase.status="withdrawn"; purchase.withdrawn_at=now; purchase.refund_cents=calc.refund_cents; purchase.stripe_refund_id=refund_id; purchase.withdrawal_note=note
        purchase.save(update_fields=("status","withdrawn_at","refund_cents","stripe_refund_id","withdrawal_note"))
        pk = purchase.pk
        transaction.on_commit(lambda: _send_after_commit(pk, now, calc, note))
        return calc, False
