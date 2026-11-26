"""Verified object subscription events. All state changes lock the object subject.

Paid access changes only after a verified invoice.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Contract, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription, Purchase
from . import stripe_service as stripe

log = logging.getLogger(__name__)


def _id(value):
    return value.get("id", "") if isinstance(value, dict) else (value or "")


def _attempt(metadata):
    return ObjectCheckoutAttempt.objects.filter(reference=metadata.get("object_checkout_attempt"),
        subject__app=getattr(settings, "DCM_BILLING_APP", "")).first()


def _matches(attempt, metadata):
    return (metadata.get("app") == attempt.subject.app and
            str(metadata.get("user_id")) == str(attempt.payer_id) and
            metadata.get("plan_key") == attempt.plan_key)


def _invoice_period(invoice):
    lines = ((invoice.get("lines") or {}).get("data") or [])
    periods = [line.get("period") or {} for line in lines if not line.get("proration")]
    return next(((p.get("start"), p.get("end")) for p in periods if p.get("start") and p.get("end")),
                (invoice.get("period_start"), invoice.get("period_end")))


def handle_object_event(kind, obj, metadata, event):
    """Handle an event already verified by Stripe; return whether it belongs here."""
    attempt = _attempt(metadata)
    if not attempt or not _matches(attempt, metadata):
        return False
    if kind.startswith("checkout.session."):
        from .object_checkout import update_object_attempt
        if not attempt.stripe_customer_id or _id(obj.get("customer")) != attempt.stripe_customer_id:
            return True
        update_object_attempt(kind, obj, metadata)
        if kind not in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            return True
        if obj.get("payment_status") not in ("paid", "no_payment_required"):
            return True
        if attempt.stripe_session_id and attempt.stripe_session_id != obj.get("id"):
            return True
        sid = _id(obj.get("subscription"))
        if not sid:
            return True
        remote = stripe._dict(stripe._stripe().Subscription.retrieve(sid))
        invoice = remote.get("latest_invoice") or {}
        if isinstance(invoice, str):
            invoice = stripe._dict(stripe._stripe().Invoice.retrieve(invoice))
        if not invoice or invoice.get("status") != "paid":
            return True
        return _paid(attempt, remote, invoice, event, obj)
    if kind.startswith("invoice."):
        sid = stripe._invoice_subscription_id(obj)
        if not sid:
            return True
        remote = stripe._dict(stripe._stripe().Subscription.retrieve(sid))
        if kind == "invoice.paid":
            return _paid(attempt, remote, obj, event, None)
        if kind == "invoice.payment_failed":
            return _sync(attempt, remote)
        return True
    if kind.startswith("customer.subscription."):
        # Fetch current provider state: a delayed webhook is never authoritative.
        remote = stripe._dict(stripe._stripe().Subscription.retrieve(obj.get("id")))
        # Recover paid invoices missed by webhook delivery.
        page = stripe._stripe().Invoice.list(subscription=remote["id"], status="paid", limit=100)
        invoices = page.auto_paging_iter() if hasattr(page, "auto_paging_iter") else page.data
        for paid_invoice in sorted((stripe._dict(item) for item in invoices),
                                   key=lambda item: (item.get("created") or 0, item.get("id") or "")):
            _paid(attempt, remote, paid_invoice, event, None)
        return _sync(attempt, remote)
    return True


def _valid_remote(attempt, remote):
    return bool(remote.get("id") and attempt.stripe_customer_id and
                _id(remote.get("customer")) == attempt.stripe_customer_id and
                _matches(attempt, remote.get("metadata") or {}))


def _withdrawn(sid, app):
    return (Purchase.objects.select_for_update().filter(stripe_object_id=sid, status="withdrawn").exists() or
            Contract.objects.select_for_update().filter(stripe_object_id=sid, app=app, status__in=("withdrawn", "ended")).exists())


def _initial_invoice(sid, invoice):
    if invoice.get("billing_reason") == "subscription_create":
        return invoice
    page = stripe._stripe().Invoice.list(subscription=sid, status="paid", limit=100)
    invoices = page.auto_paging_iter() if hasattr(page, "auto_paging_iter") else page.data
    for item in invoices:
        item = stripe._dict(item)
        if (item.get("status") == "paid" and item.get("billing_reason") == "subscription_create"
                and _id(stripe._invoice_subscription_id(item)) == sid):
            return item
    raise ValueError("object_initial_paid_invoice_missing")


def _paid(attempt, remote, invoice, event, session):
    if (invoice.get("status") != "paid" or not _valid_remote(attempt, remote)
            or _id(stripe._invoice_subscription_id(invoice)) != remote["id"]):
        return True
    start, end = _invoice_period(invoice)
    if not start or not end or int(end) <= int(start):
        return True
    with transaction.atomic():
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=attempt.subject_id)
        attempt = subject.attempts.get(pk=attempt.pk)
        sid = remote["id"]
        if _withdrawn(sid, subject.app):
            return True
        if attempt.status in ("failed", "expired") and subject.attempts.exclude(pk=attempt.pk).filter(
                status__in=("open", "processing", "paid", "completed")).exists():
            return True
        existing = ObjectSubscription.objects.filter(stripe_subscription_id=sid).first()
        if existing and (existing.subject_id != subject.pk or existing.payer_id != attempt.payer_id):
            return True
        if subject.subscriptions.filter(blocks_purchase=True).exclude(stripe_subscription_id=sid).exists():
            return True
        contract = Contract.objects.filter(stripe_object_id=sid, app=subject.app).first()
        start_at, end_at = stripe._utc(start), stripe._utc(end)
        invoice_plan = stripe._invoice_plan_key(invoice)
        current_plan = existing.plan_key if existing else attempt.plan_key
        if start_at > timezone.now() or (invoice_plan and invoice_plan != current_plan):
            log.error("Guarded paid object invoice=%s subscription=%s", invoice.get("id"), sid)
            return True
        if contract and contract.cancel_effective_at and start_at >= contract.cancel_effective_at:
            return True
        if contract:
            stripe._record_contract_payment(contract, invoice, "initial" if invoice.get("billing_reason") == "subscription_create" else "renewal")
        plan_key = invoice_plan or attempt.plan_key
        plan = stripe._plan(plan_key)
        if not plan or plan.kind != "object_subscription":
            return True
        if existing and existing.current_period_end and end_at <= existing.current_period_end:
            return True
        sub, _ = ObjectSubscription.objects.update_or_create(stripe_subscription_id=sid, defaults={
            "subject": subject, "payer": attempt.payer, "plan_key": plan_key,
            "stripe_customer_id": attempt.stripe_customer_id,
            "status": remote.get("status", "active"), "current_period_start": start_at,
            "current_period_end": end_at, "cancel_at_period_end": bool(remote.get("cancel_at_period_end")),
            "ended_at": stripe._utc(remote.get("ended_at")), "blocks_purchase": True})
        attempt.status = "completed"
        attempt.save(update_fields=("status", "updated_at"))
        if not contract:
            initial = _initial_invoice(sid, invoice)
            initial_start, initial_end = _invoice_period(initial)
            if not initial_start or not initial_end or int(initial_end) <= int(initial_start):
                raise ValueError("object_initial_invoice_period_missing")
            purchase_data = dict(session or {})
            purchase_data.setdefault("metadata", remote.get("metadata") or {})
            purchase_data["amount_total"] = initial.get("amount_paid")
            purchase_data["currency"] = initial.get("currency")
            initial_plan = stripe._plan(stripe._invoice_plan_key(initial) or attempt.plan_key)
            if not initial_plan or initial_plan.kind != "object_subscription":
                raise ValueError("object_initial_plan_missing")
            purchase_event = dict(event, created=initial.get("created") or initial_start)
            purchase = stripe._create_purchase(purchase_data, purchase_event, attempt.payer, initial_plan, "object_subscription", sid,
                payment_intent=stripe._invoice_payment_intent(initial), invoice_id=initial.get("id", ""),
                term_start=stripe._utc(initial_start), term_end=stripe._utc(initial_end), invoice_data=initial)
            if initial.get("id") != invoice.get("id"):
                stripe._record_contract_payment(purchase.contract, invoice, "renewal")
        return True


def _sync(attempt, remote):
    if not _valid_remote(attempt, remote):
        return True
    with transaction.atomic():
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=attempt.subject_id)
        sid = remote["id"]
        if _withdrawn(sid, subject.app):
            return True
        sub = subject.subscriptions.filter(stripe_subscription_id=sid, payer=attempt.payer).first()
        if not sub:
            return True  # Subscription state alone is not proof of payment.
        sub.status = remote.get("status", sub.status)
        # Schedule end_behavior=cancel does not necessarily set the subscription's
        # cancel_at_period_end flag. Preserve a confirmed contract cancellation.
        scheduled_cancel = Contract.objects.filter(
            app=subject.app, stripe_object_id=sid, status="cancellation_scheduled",
            cancel_effective_at=sub.current_period_end,
        ).exists() if sub.current_period_end else False
        sub.cancel_at_period_end = bool(remote.get("cancel_at_period_end")) or scheduled_cancel
        sub.ended_at = stripe._utc(remote.get("ended_at"))
        sub.blocks_purchase = not (sub.status in ("canceled", "incomplete_expired") and
                                   (not sub.current_period_end or sub.current_period_end <= timezone.now()))
        sub.save(update_fields=("status", "cancel_at_period_end", "ended_at", "blocks_purchase"))
    return True
