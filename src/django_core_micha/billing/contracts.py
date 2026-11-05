"""Contract lifecycle, cancellation, full-refund withdrawal, and continuation guards."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from calendar import monthrange

from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone

from .config import load_billing_config
from .models import (Contract, ContractPayment, Declaration, Purchase, Refund, StripePrice,
                     Subscription, TeamPackage, TokenAccount, TokenEntry)
from .withdrawal import EU_EEA_COUNTRIES

log=logging.getLogger(__name__)

# A refund Stripe definitively failed carries `refund_failed`. Rows written by 3.21.0's refund.failed webhook carry the
# event name instead; both are retried with a fresh row and key, so no existing row is orphaned.
REFUND_FAILED_CODES=("refund_failed","refund.failed")


def _app():
    return getattr(settings,"DCM_BILLING_APP","")


@dataclass(frozen=True)
class ContractEligibility:
    eligible: bool
    reason: str
    deadline: datetime


def _add_months(value, months):
    month=value.month-1+months
    year=value.year+month//12
    month=month%12+1
    return value.replace(year=year,month=month,day=min(value.day,monthrange(year,month)[1]))


def withdrawal_deadline(contract):
    if contract.info_delivered_at:
        start=max(contract.started_at,contract.info_delivered_at)
        return start+timedelta(days=14)
    return _add_months(contract.started_at,12)+timedelta(days=14)


def create_contract_for_purchase(purchase, plan, *, info_delivered=False):
    cfg=load_billing_config(); policy=cfg.contract or {}
    continuation=next((p for p in cfg.plans if p.key==getattr(plan,"continuation",None)),None)
    opted=bool(policy and continuation)
    continuation_price=StripePrice.objects.filter(app=cfg.app,plan_key=continuation.key).order_by("-version").first() if opted else None
    country=purchase.billing_country or ""
    consumer="consumer" if country in EU_EEA_COUNTRIES and not purchase.has_business_tax_id else "unknown"
    contract,created=Contract.objects.get_or_create(purchase=purchase,defaults={
        "user":purchase.user,"app":cfg.app,"kind":purchase.kind,"plan_key":purchase.plan_key,
        "stripe_object_id":purchase.stripe_object_id,"terms_version":policy.get("terms_version", ""),
        "seller":policy.get("seller", ""),"withdrawal_policy":policy.get("withdrawal_refund","prorated"),
        "consumer_status":consumer,"billing_country":country,"started_at":purchase.term_start or purchase.purchased_at,
        "info_delivered_at":purchase.purchased_at if info_delivered else None,"initial_term_end":purchase.term_end,
        "continuation_plan_key":continuation.key if opted else "",
        "continuation_stripe_price_id":continuation_price.stripe_price_id if continuation_price else "",
        "continuation_amount_cents":continuation.amount_cents if opted else None,
        "continuation_token_allowance":continuation.token_allowance if opted else None,
        "continuation_interval":continuation.interval if opted else "","transition_guard_days":policy.get("transition_guard_days",7),
        "phase":"initial" if opted else "none","schedule_status":"pending" if opted else "not_needed"})
    if created and opted:
        transaction.on_commit(lambda pk=contract.pk: create_continuation_schedule(pk))
    return contract,created


def withdrawal_eligibility(contract, now=None):
    now=now or timezone.now()
    deadline=withdrawal_deadline(contract)
    if not getattr(settings,"DCM_BILLING_WITHDRAWAL_ENABLED",True): return ContractEligibility(False,"withdrawal_disabled",deadline)
    if contract.status not in ("active","cancellation_scheduled"): return ContractEligibility(False,"not_active",deadline)
    if contract.billing_country not in EU_EEA_COUNTRIES: return ContractEligibility(False,"country_not_eligible",deadline)
    if contract.consumer_status != "consumer": return ContractEligibility(False,"review_required",deadline)
    if now > deadline: return ContractEligibility(False,"period_over",deadline)
    return ContractEligibility(True,"eligible",deadline)


def contract_summary(contract, now=None):
    now=now or timezone.now()
    cfg=load_billing_config(); plans={p.key:p for p in cfg.plans}
    plan=plans.get(contract.plan_key)
    sub=Subscription.objects.filter(stripe_subscription_id=contract.stripe_object_id).first() if contract.kind=="subscription" else None
    current_end=sub.current_period_end if sub else contract.initial_term_end
    if contract.continuation_plan_key and contract.phase in ("initial","continuation"):
        amount=contract.continuation_amount_cents; allowance=contract.continuation_token_allowance
    else:
        amount=plan.amount_cents if plan else None; allowance=plan.token_allowance if plan else None
    deadline=withdrawal_deadline(contract)
    if contract.withdrawal_policy=="full":
        elig=withdrawal_eligibility(contract,now)
    else:
        from .withdrawal import eligibility
        result=eligibility(contract.purchase,now) if contract.purchase_id else None
        elig=ContractEligibility(result.eligible if result else False,result.reason if result else "not_active",
            contract.started_at+timedelta(days=14))
        deadline=elig.deadline
    can_charge=(contract.kind=="subscription" and contract.status in ("active","cancellation_scheduled") and current_end is not None
        and (contract.status=="active" or not contract.cancel_effective_at or contract.cancel_effective_at>current_end)
        and (not contract.continuation_plan_key or contract.phase!="initial" or contract.schedule_status=="created"))
    return {"reference":contract.reference,"kind":contract.kind,"plan_key":contract.plan_key,
        "plan_name":plan.name if plan else "","phase":contract.phase,"status":contract.status,
        "started_at":contract.started_at.isoformat(),"initial_term_end":contract.initial_term_end.isoformat() if contract.initial_term_end else None,
        "current_period_end":current_end.isoformat() if current_end else None,
        "next_charge":{"amount_cents":amount,"currency":cfg.currency,"date":current_end.isoformat(),
            "token_allowance":allowance} if can_charge and amount is not None else None,
        "cancel_effective_at":contract.cancel_effective_at.isoformat() if contract.cancel_effective_at else None,
        "withdrawal":{"eligible":elig.eligible,"reason":elig.reason,"deadline":elig.deadline.isoformat()},
        "declarations":[{"reference":d.reference,"kind":d.kind,"status":d.status,"received_at":d.received_at.isoformat(),
            "effective_at":d.effective_at.isoformat() if d.effective_at else None} for d in contract.declarations.order_by("received_at","pk")],
        "refunds":[{"status":r.status,"amount_cents":r.amount_cents,"currency":r.currency,"due_by":r.due_by.isoformat()} for r in contract.refunds.order_by("created_at","pk")]}


def _local_date(value):
    if timezone.is_aware(value): return timezone.localtime(value).date()
    return value.date()


def _period_end(contract):
    if contract.kind == "subscription":
        sub=Subscription.objects.filter(stripe_subscription_id=contract.stripe_object_id).first()
        if sub and sub.current_period_end: return sub.current_period_end
    return contract.initial_term_end


def _cancellation_effective(contract, requested_date):
    current=_period_end(contract) or contract.initial_term_end or timezone.now()
    if not requested_date or requested_date <= _local_date(current): return current
    if contract.kind != "subscription": return contract.initial_term_end or current
    end=current; anchor_day=current.day; month_end=current.day==monthrange(current.year,current.month)[1]
    while _local_date(end) < requested_date:
        month=end.month
        year=end.year+int(month==12); month=month%12+1
        last=monthrange(year,month)[1]
        end=end.replace(year=year,month=month,day=last if month_end else min(anchor_day,last))
    return end


def _schedule_phase(schedule):
    data=schedule if isinstance(schedule,dict) else schedule.to_dict_recursive()
    phases=data.get("phases") or []
    if not phases:
        raise ValueError("schedule_phase_missing")
    phase=phases[0]
    result={key:phase[key] for key in ("items","start_date","end_date","trial_end","billing_cycle_anchor","proration_behavior","metadata","coupon","discounts","default_tax_rates","tax_rates","automatic_tax","transfer_data","application_fee_percent","on_behalf_of","invoice_settings") if key in phase}
    return result


def create_continuation_schedule(contract_id):
    contract=Contract.objects.select_related("purchase").get(pk=contract_id)
    if not contract.continuation_plan_key or contract.schedule_status == "created": return contract
    from .stripe_service import _latest_price,_stripe
    try:
        price_id=contract.continuation_stripe_price_id
        if not price_id:
            price_id=_latest_price(contract.continuation_plan_key).stripe_price_id
            contract.continuation_stripe_price_id=price_id
            contract.save(update_fields=("continuation_stripe_price_id","updated_at"))
        stripe=_stripe()
        schedule=stripe.SubscriptionSchedule.create(from_subscription=contract.stripe_object_id,
            idempotency_key=f"dcm-schedule:{contract.app}:{contract.stripe_object_id}")
        schedule_id=schedule.get("id") if isinstance(schedule,dict) else schedule.id
        phase1=_schedule_phase(schedule)
        phase2={"items":[{"price":price_id,"quantity":1}],
            "duration":{"interval":"month","interval_count":1},"proration_behavior":"none","metadata":{"app":contract.app,"user_id":str(contract.user_id),"plan_key":contract.continuation_plan_key}}
        updated=stripe.SubscriptionSchedule.modify(schedule_id,end_behavior="release",phases=[phase1,phase2])
        updated_id=updated.get("id",schedule_id) if isinstance(updated,dict) else updated.id
        latest=Contract.objects.get(pk=contract.pk)
        if latest.status=="withdrawn":
            try: stripe.SubscriptionSchedule.release(updated_id)
            except Exception: pass
            try: stripe.Subscription.cancel(latest.stripe_object_id)
            except Exception: pass
            Contract.objects.filter(pk=contract.pk).update(stripe_schedule_id=updated_id,schedule_status="cancelled")
        elif latest.status=="cancellation_scheduled" and latest.cancel_effective_at and latest.initial_term_end and latest.cancel_effective_at<=latest.initial_term_end:
            try:
                current=stripe.SubscriptionSchedule.retrieve(updated_id)
                stripe.SubscriptionSchedule.modify(updated_id,end_behavior="cancel",phases=[_schedule_phase(current)])
            except Exception as exc:
                Contract.objects.filter(pk=contract.pk).update(stripe_schedule_id=updated_id,schedule_status="failed",needs_review=True,review_reason="cancellation_schedule_race")
                log.error("Schedule cancellation race failed contract=%s error=%s",contract.pk,type(exc).__name__)
            else:
                Contract.objects.filter(pk=contract.pk).update(stripe_schedule_id=updated_id,schedule_status="created",needs_review=False,review_reason="")
        else:
            Contract.objects.filter(pk=contract.pk).update(stripe_schedule_id=updated_id,schedule_status="created",needs_review=False,review_reason="")
    except Exception as exc:
        Contract.objects.filter(pk=contract.pk).update(schedule_status="failed",needs_review=True,review_reason="transition_schedule_failed")
        log.error("Continuation schedule failed contract=%s error=%s",contract.pk,type(exc).__name__)
    return Contract.objects.get(pk=contract.pk)


def ensure_contract_schedule(contract):
    if not contract.continuation_plan_key: return contract
    if contract.schedule_status not in ("pending","failed"): return contract
    return create_continuation_schedule(contract.pk)


def apply_cancellation(declaration, *, operator_accepted=False):
    contract=declaration.contract
    if not contract: return declaration
    if declaration.cancellation_type=="extraordinary" and not operator_accepted:
        contract.needs_review=True; contract.review_reason="extraordinary_cancellation"
        contract.save(update_fields=("needs_review","review_reason","updated_at"))
        declaration.status="needs_review"; declaration.save(update_fields=("status",))
        return declaration
    if contract.kind != "subscription":
        effective=contract.initial_term_end or timezone.now()
        declaration.effective_at=effective; declaration.status="effective"
        contract.cancel_effective_at=effective; contract.status="cancellation_scheduled"
        declaration.save(update_fields=("effective_at","status")); contract.save(update_fields=("cancel_effective_at","status","updated_at"))
        return declaration
    effective=_cancellation_effective(contract,declaration.requested_end_date)
    sub=Subscription.objects.filter(stripe_subscription_id=contract.stripe_object_id).first()
    if not sub:
        contract.needs_review=True; contract.review_reason="subscription_missing"
        contract.save(update_fields=("needs_review","review_reason","updated_at"))
        declaration.status="needs_review"; declaration.save(update_fields=("status",))
        return declaration
    now=timezone.now()
    if effective > (sub.current_period_end or now):
        declaration.status="scheduled"; declaration.effective_at=effective
        contract.status="cancellation_scheduled"; contract.cancel_effective_at=effective
        declaration.save(update_fields=("status","effective_at")); contract.save(update_fields=("status","cancel_effective_at","updated_at"))
        return declaration
    try:
        from .stripe_service import _stripe
        stripe=_stripe()
        if contract.phase=="initial" and contract.stripe_schedule_id and contract.schedule_status=="created":
            schedule=stripe.SubscriptionSchedule.retrieve(contract.stripe_schedule_id)
            phase1=_schedule_phase(schedule)
            stripe.SubscriptionSchedule.modify(contract.stripe_schedule_id,end_behavior="cancel",phases=[phase1])
            sub.cancel_at_period_end=True; sub.save(update_fields=("cancel_at_period_end",))
        else:
            stripe.Subscription.modify(contract.stripe_object_id,cancel_at_period_end=True)
            sub.cancel_at_period_end=True; sub.save(update_fields=("cancel_at_period_end",))
    except Exception as exc:
        declaration.status="needs_review"; declaration.decision_note=type(exc).__name__
        declaration.save(update_fields=("status","decision_note"))
        contract.needs_review=True; contract.review_reason="cancellation_provider_failed"
        contract.save(update_fields=("needs_review","review_reason","updated_at"))
        return declaration
    declaration.status="scheduled"; declaration.effective_at=effective
    contract.status="cancellation_scheduled"; contract.cancel_effective_at=effective
    declaration.save(update_fields=("status","effective_at")); contract.save(update_fields=("status","cancel_effective_at","updated_at"))
    return declaration


def _owner_accounts(contract):
    if contract.kind=="team_package": return TokenAccount.objects.filter(team_package__stripe_ref=contract.stripe_object_id)
    return TokenAccount.objects.filter(user=contract.user)


def _contract_grants(contract):
    refs=[]
    if contract.purchase_id: refs.extend(contract.purchase.grant_references or [])
    if contract.kind=="subscription":
        return TokenEntry.objects.filter(account__in=_owner_accounts(contract),kind__in=("grant","purchase")).filter(
            Q(reference__in=refs)|Q(reference__startswith=f"stripe:{contract.stripe_object_id}:"))
    if contract.kind=="team_package":
        refs.append(f"stripe:team:{contract.stripe_object_id}")
    return TokenEntry.objects.filter(account__in=_owner_accounts(contract),kind__in=("grant","purchase"),reference__in=refs)


def _stop_future_charges(contract, now):
    if contract.kind=="subscription":
        from .stripe_service import _stripe
        stripe=_stripe()
        try: stripe.Subscription.cancel(contract.stripe_object_id)
        except Exception as exc:
            if getattr(exc,"http_status",None)!=404 and "already canceled" not in str(exc).lower(): raise
        if contract.stripe_schedule_id:
            try: stripe.SubscriptionSchedule.release(contract.stripe_schedule_id)
            except Exception as exc:
                if getattr(exc,"http_status",None)!=404 and "released" not in str(exc).lower() and "canceled" not in str(exc).lower(): raise
            Contract.objects.filter(pk=contract.pk).update(schedule_status="released")
        Subscription.objects.filter(stripe_subscription_id=contract.stripe_object_id).update(status="canceled",ended_at=now,current_period_end=now,cancel_at_period_end=False)
    elif contract.kind=="team_package":
        TeamPackage.objects.filter(stripe_ref=contract.stripe_object_id).update(ends_at=now)


def _stripe_refunded_cents(payment):
    amount=int(payment.refunded_cents or 0)
    if not payment.stripe_payment_intent_id: return amount
    from .stripe_service import _dict,_stripe
    stripe=_stripe()
    pi=_dict(stripe.PaymentIntent.retrieve(payment.stripe_payment_intent_id))
    charge=pi.get("latest_charge")
    if isinstance(charge,dict): charge=charge.get("id")
    if charge:
        charge_data=_dict(stripe.Charge.retrieve(charge))
        amount=max(amount,int(charge_data.get("amount_refunded") or 0))
    return amount


def _refund_row(declaration,payment,refunded,amount_override=None):
    amount=max(0,int(payment.amount_paid_cents)-int(refunded))
    if amount_override is not None: amount=min(amount,max(0,int(amount_override)))
    if not amount: return None
    row=Refund.objects.filter(payment=payment,declaration=declaration).exclude(status__in=("succeeded","cancelled")).first()
    if row: return row
    try:
        with transaction.atomic():
            list(ContractPayment.objects.select_for_update().filter(pk=payment.pk).values_list("pk",flat=True))
            existing=Refund.objects.filter(payment=payment,declaration=declaration).exclude(status__in=("succeeded","cancelled")).first()
            if existing: return existing
            row=Refund.objects.create(contract=declaration.contract,payment=payment,declaration=declaration,
                amount_cents=amount,currency=payment.currency,idempotency_key=f"dcm-refund:pending:{payment.pk}:{declaration.pk}",
                due_by=declaration.received_at+timedelta(days=14),status="pending")
            row.idempotency_key=f"dcm-refund:{declaration.app}:{row.pk}"
            row.save(update_fields=("idempotency_key","updated_at"))
    except IntegrityError:
        return Refund.objects.filter(payment=payment,declaration=declaration).exclude(status__in=("succeeded","cancelled")).first()
    return row


def process_refund(refund_id):
    with transaction.atomic():
        row=Refund.objects.select_for_update().select_related("payment","contract").get(pk=refund_id)
        if row.status in ("succeeded","cancelled"): return row
        row.attempts+=1; row.status="pending"; row.last_error_code=""
        row.save(update_fields=("attempts","status","last_error_code","updated_at"))
    if not row.payment.stripe_payment_intent_id:
        with transaction.atomic():
            row=Refund.objects.select_for_update().get(pk=refund_id)
            if row.status!="succeeded": row.last_error_code="payment_reference_missing"; row.status="failed"; row.save(update_fields=("status","last_error_code","updated_at"))
        return row
    result_status="pending"; refund_id_remote=""; error_code=""
    try:
        from .stripe_service import _stripe
        result=_stripe().Refund.create(payment_intent=row.payment.stripe_payment_intent_id,amount=row.amount_cents,
            metadata={"app":row.contract.app,"contract_id":str(row.contract_id),"refund_id":str(row.pk),"dcm_refund":row.idempotency_key},
            reason="requested_by_customer",idempotency_key=row.idempotency_key)
        data=result.to_dict_recursive() if hasattr(result,"to_dict_recursive") else (result if isinstance(result,dict) else vars(result))
        result_status=data.get("status") or "pending"; refund_id_remote=data.get("id") or ""
    except Exception as exc:
        # A timeout may follow provider acceptance. Keep the fixed row and key for a safe retry.
        error_code=type(exc).__name__
        log.error("Refund request failed refund=%s error=%s",row.pk,type(exc).__name__)
    with transaction.atomic():
        locked=Refund.objects.select_for_update().select_related("payment").get(pk=refund_id)
        if locked.status!="succeeded":
            next_status={"succeeded":"succeeded","pending":"requested","failed":"failed","requires_action":"requires_action"}.get(result_status,"requested") if not error_code else "pending"
            if next_status=="succeeded":
                payment=type(locked.payment).objects.select_for_update().get(pk=locked.payment_id)
                payment.refunded_cents=int(payment.refunded_cents or 0)+locked.amount_cents
                payment.save(update_fields=("refunded_cents",))
            locked.status=next_status; locked.stripe_refund_id=refund_id_remote or locked.stripe_refund_id
            locked.last_error_code=error_code or ("refund_failed" if next_status=="failed" else "")
            locked.save(update_fields=("stripe_refund_id","status","last_error_code","updated_at"))
        return locked


def apply_withdrawal(declaration, *, operator_accepted=False):
    contract=declaration.contract
    if not contract: return declaration
    eligible=withdrawal_eligibility(contract,declaration.received_at)
    if not eligible.eligible and not operator_accepted:
        contract.needs_review=True; contract.review_reason=eligible.reason
        contract.save(update_fields=("needs_review","review_reason","updated_at"))
        declaration.status="needs_review"; declaration.decision_note=eligible.reason
        declaration.save(update_fields=("status","decision_note"))
        return declaration
    # First persist the declaration and withdrawal state; provider calls follow this commit.
    declaration.status="accepted"; declaration.save(update_fields=("status",))
    contract.status="withdrawn"; contract.save(update_fields=("status","updated_at"))
    if contract.purchase_id:
        Purchase.objects.filter(pk=contract.purchase_id).update(status="withdrawn",withdrawn_at=declaration.received_at)
    transaction.on_commit(lambda: _perform_withdrawal(declaration.pk))
    return declaration


def _perform_withdrawal(declaration_id):
    declaration=Declaration.objects.select_related("contract","contract__purchase").get(pk=declaration_id)
    contract=declaration.contract; now=declaration.received_at
    try:
        _stop_future_charges(contract,now)
    except Exception as exc:
        Contract.objects.filter(pk=contract.pk).update(needs_review=True,review_reason="withdrawal_cancel_failed")
        log.error("Withdrawal cancellation failed declaration=%s error=%s",declaration.pk,type(exc).__name__)
        return
    with transaction.atomic():
        list(_owner_accounts(contract).select_for_update().values_list("pk",flat=True))
        for entry in _contract_grants(contract).select_for_update().filter(remaining__gt=0):
            left=int(entry.remaining or 0)
            if left:
                entry.remaining=0; entry.save(update_fields=("remaining",))
                TokenEntry.objects.get_or_create(account=entry.account,kind="expiry",reference=f"withdrawal:{declaration.reference}:{entry.pk}",
                    defaults={"amount":-left,"purpose":"withdrawal","app":contract.app})
    if contract.kind=="team_package": TeamPackage.objects.filter(stripe_ref=contract.stripe_object_id).update(ends_at=now)
    remaining_override=declaration.decision_refund_cents
    for payment in contract.payments.all().order_by("pk"):
        try: refunded=_stripe_refunded_cents(payment)
        except Exception as exc:
            Contract.objects.filter(pk=contract.pk).update(needs_review=True,review_reason="refund_balance_lookup_failed")
            log.error("Refund balance lookup failed declaration=%s payment=%s error=%s",declaration.pk,payment.pk,type(exc).__name__)
            continue
        payment.refunded_cents=max(payment.refunded_cents,refunded); payment.save(update_fields=("refunded_cents",))
        override=None
        if remaining_override is not None:
            override=remaining_override
        row=_refund_row(declaration,payment,refunded,override)
        if remaining_override is not None and row:
            remaining_override=max(0,remaining_override-row.amount_cents)
        if row: retry_refund(row.pk)  # a failed row gets a fresh key; replaying it would return Stripe's failed result again
    payments=list(contract.payments.all())
    if declaration.decision_refund_cents is not None:
        complete=all(not payment.refunds.exclude(status__in=("succeeded","cancelled")).exists() for payment in payments)
    else:
        complete=all(int(payment.refunded_cents or 0)>=int(payment.amount_paid_cents) for payment in payments)
    if complete:
        declaration.status="effective"; declaration.effective_at=declaration.received_at
        declaration.save(update_fields=("status","effective_at"))


def apply_due_cancellations(now=None):
    now=now or timezone.now()
    count=0
    for contract in Contract.objects.filter(app=_app(),kind__in=("team_package","token_package"),status="cancellation_scheduled",cancel_effective_at__lte=now):
        contract.status="ended"; contract.save(update_fields=("status","updated_at")); count+=1
    for declaration in Declaration.objects.filter(app=_app(),kind="cancellation",status="scheduled").select_related("contract"):
        contract=declaration.contract
        if not contract: continue
        if contract.kind!="subscription": continue
        sub=Subscription.objects.filter(stripe_subscription_id=contract.stripe_object_id).first()
        if not sub or not sub.current_period_end: continue
        if sub.status=="canceled" or (sub.current_period_end and sub.current_period_end<=now):
            declaration.status="effective"; declaration.effective_at=declaration.effective_at or sub.ended_at or sub.current_period_end or now
            declaration.save(update_fields=("status","effective_at"))
            if sub.status!="canceled":
                # The period ended before Stripe's deletion webhook: end the local subscription now. The webhook is idempotent.
                Subscription.objects.filter(pk=sub.pk).update(status="canceled",ended_at=sub.ended_at or sub.current_period_end,cancel_at_period_end=False)
            contract.status="ended"; contract.save(update_fields=("status","updated_at")); count+=1
            continue
        if declaration.cancellation_type!="extraordinary" and sub.current_period_end >= declaration.effective_at and sub.current_period_end > now and not (sub.cancel_at_period_end and contract.cancel_effective_at==sub.current_period_end):
            try:
                from .stripe_service import _stripe
                _stripe().Subscription.modify(contract.stripe_object_id,cancel_at_period_end=True)
                sub.cancel_at_period_end=True; sub.save(update_fields=("cancel_at_period_end",))
                declaration.effective_at=sub.current_period_end; declaration.save(update_fields=("effective_at",))
                contract.cancel_effective_at=sub.current_period_end; contract.save(update_fields=("cancel_effective_at","updated_at")); count+=1
            except Exception as exc:
                log.error("Due cancellation failed declaration=%s error=%s",declaration.pk,type(exc).__name__)
    return count


def run_transition_guard(now=None):
    now=now or timezone.now()
    created=list(Contract.objects.filter(app=_app(),kind="subscription",phase="initial",continuation_plan_key__gt="",schedule_status="created",status="active"))
    for contract in created:
        if not contract.initial_term_end or contract.initial_term_end>now+timedelta(days=contract.transition_guard_days): continue
        try:
            from .stripe_service import _dict,_stripe
            remote=_dict(_stripe().SubscriptionSchedule.retrieve(contract.stripe_schedule_id))
            if remote.get("status") not in ("active","not_started"):
                contract.schedule_status="failed"; contract.save(update_fields=("schedule_status","updated_at"))
        except Exception as exc:
            contract.schedule_status="failed"; contract.save(update_fields=("schedule_status","updated_at"))
            log.error("Continuation schedule lookup failed contract=%s error=%s",contract.pk,type(exc).__name__)
    qs=Contract.objects.filter(app=_app(),kind="subscription",phase="initial",continuation_plan_key__gt="",schedule_status__in=("pending","failed"),status="active")
    count=0
    for contract in qs:
        if not contract.initial_term_end or contract.initial_term_end>now+timedelta(days=contract.transition_guard_days): continue
        try:
            from .stripe_service import _stripe
            _stripe().Subscription.modify(contract.stripe_object_id,cancel_at_period_end=True)
            contract.needs_review=True; contract.review_reason="transition_schedule_missing"
            sub=Subscription.objects.filter(stripe_subscription_id=contract.stripe_object_id).first()
            if sub and sub.current_period_end:
                contract.cancel_effective_at=sub.current_period_end
                contract.status="cancellation_scheduled"
                sub.cancel_at_period_end=True; sub.save(update_fields=("cancel_at_period_end",))
            contract.save(update_fields=("needs_review","review_reason","cancel_effective_at","status","updated_at")); count+=1
        except Exception as exc:
            Contract.objects.filter(pk=contract.pk).update(needs_review=True,review_reason="transition_schedule_missing")
            log.error("Transition guard failed contract=%s error=%s",contract.pk,type(exc).__name__)
    return count


def resume_withdrawals():
    count=0
    for declaration in Declaration.objects.filter(app=_app(),kind="withdrawal",status="accepted",contract__status="withdrawn").values_list("pk",flat=True):
        _perform_withdrawal(declaration); count+=1
    return count


def retry_refund(refund_id):
    """Retry one open refund. A request Stripe definitively failed gets a fresh row and key instead of replaying the failed one."""
    row=Refund.objects.select_related("payment","declaration").get(pk=refund_id)
    if row.status=="failed" and row.last_error_code in REFUND_FAILED_CODES and row.declaration_id:
        with transaction.atomic():
            locked=Refund.objects.select_for_update().get(pk=row.pk)
            if locked.status!="failed" or locked.last_error_code not in REFUND_FAILED_CODES: return locked
            locked.status="cancelled"; locked.save(update_fields=("status","updated_at"))
        fresh=_refund_row(row.declaration,row.payment,int(row.payment.refunded_cents or 0),row.amount_cents)
        return process_refund(fresh.pk) if fresh else row
    return process_refund(row.pk)


def _allocate_refund(declaration_id,total):
    """Refund `total` cents of an operator-accepted termination, newest payment first, one fixed row per payment."""
    declaration=Declaration.objects.select_related("contract").get(pk=declaration_id)
    remaining=int(total)
    for payment in declaration.contract.payments.all().order_by("-paid_at","-pk"):
        if remaining<=0: break
        try: refunded=_stripe_refunded_cents(payment)
        except Exception as exc:
            Contract.objects.filter(pk=declaration.contract_id).update(needs_review=True,review_reason="refund_balance_lookup_failed")
            log.error("Refund balance lookup failed declaration=%s payment=%s error=%s",declaration.pk,payment.pk,type(exc).__name__)
            return
        payment.refunded_cents=max(payment.refunded_cents,refunded); payment.save(update_fields=("refunded_cents",))
        row=_refund_row(declaration,payment,refunded,remaining)
        if row:
            remaining-=row.amount_cents
            retry_refund(row.pk)


def apply_extraordinary_termination(declaration, effective_date=None, refund_cents=None):
    """Operator-accepted extraordinary termination: end the contract on `effective_date` (today or earlier = now) and refund
    `refund_cents` of prepaid time. The declaration keeps its receipt time."""
    contract=declaration.contract
    now=timezone.now()
    effective=timezone.make_aware(datetime.combine(effective_date,time.min)) if effective_date else now
    immediate=effective<=now
    try:
        if immediate:
            _stop_future_charges(contract,now)
        elif contract.kind=="subscription":
            from .stripe_service import _stripe
            stripe=_stripe()
            if contract.stripe_schedule_id and contract.schedule_status=="created":
                stripe.SubscriptionSchedule.release(contract.stripe_schedule_id)
                Contract.objects.filter(pk=contract.pk).update(schedule_status="released")
            stripe.Subscription.modify(contract.stripe_object_id,cancel_at=int(effective.timestamp()),proration_behavior="none")
        elif contract.kind=="team_package":
            TeamPackage.objects.filter(stripe_ref=contract.stripe_object_id).update(ends_at=effective)
    except Exception as exc:
        declaration.status="needs_review"; declaration.decision_note=type(exc).__name__
        declaration.save(update_fields=("status","decision_note"))
        Contract.objects.filter(pk=contract.pk).update(needs_review=True,review_reason="termination_provider_failed")
        log.error("Extraordinary termination failed declaration=%s error=%s",declaration.pk,type(exc).__name__)
        return declaration
    contract.refresh_from_db()
    contract.status="ended" if immediate else "cancellation_scheduled"; contract.cancel_effective_at=now if immediate else effective
    contract.needs_review=False; contract.review_reason=""
    contract.save(update_fields=("status","cancel_effective_at","needs_review","review_reason","updated_at"))
    declaration.status="effective" if immediate else "scheduled"; declaration.effective_at=contract.cancel_effective_at
    declaration.save(update_fields=("status","effective_at"))
    if refund_cents:
        transaction.on_commit(lambda pk=declaration.pk,total=int(refund_cents): _allocate_refund(pk,total))
    return declaration
