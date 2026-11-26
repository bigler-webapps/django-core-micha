"""Recover and refund the payments belonging to one withdrawn object contract."""
import logging
from math import ceil

from .models import Contract

log = logging.getLogger(__name__)


def _invoices(contract, received_at):
    from . import stripe_service as stripe
    from .object_lifecycle import _invoice_period

    page = stripe._stripe().Invoice.list(subscription=contract.stripe_object_id, status="paid", limit=100)
    items = page.auto_paging_iter() if hasattr(page, "auto_paging_iter") else page.data
    invoices = {}
    initial_found = False
    for item in items:
        invoice = stripe._dict(item)
        sid = stripe._invoice_subscription_id(invoice)
        if isinstance(sid, dict):
            sid = sid.get("id")
        paid_at = (invoice.get("status_transitions") or {}).get("paid_at") or invoice.get("created")
        if invoice.get("status") != "paid" or sid != contract.stripe_object_id or not invoice.get("id"):
            continue
        if not paid_at:
            raise ValueError("invoice_paid_time_missing")
        if stripe._utc(paid_at) > received_at:
            continue
        start, end = _invoice_period(invoice)
        if contract.withdrawal_policy == "prorated" and (not start or not end or int(end) <= int(start)):
            raise ValueError("invoice_period_missing")
        payment = stripe._record_contract_payment(contract, invoice,
            "initial" if invoice.get("billing_reason") == "subscription_create" else "renewal")
        if payment.contract_id != contract.pk:
            raise ValueError("invoice_contract_mismatch")
        invoices[payment.pk] = (start, end)
        initial_found |= invoice.get("billing_reason") == "subscription_create"
    if not initial_found:
        raise ValueError("object_initial_paid_invoice_missing")
    if any(p.pk not in invoices for p in contract.payments.filter(paid_at__lte=received_at)):
        raise ValueError("contract_invoice_missing")
    return invoices


def _target(payment, period, now, policy):
    amount = int(payment.amount_paid_cents)
    if policy == "full" or not amount:
        return amount
    start, end = map(int, period)
    days = max(1, ceil((end - start) / 86400))
    used = min(days, max(0, ceil((now.timestamp() - start) / 86400)))
    return amount - (used * amount + days - 1) // days


def object_withdrawal_preview(contract, now):
    """Recover the same payments and provider refund balances used by execution."""
    from .contracts import _stripe_refunded_cents

    periods = _invoices(contract, now)
    payments = list(contract.payments.filter(pk__in=periods).order_by("paid_at", "pk"))
    refunded = [_stripe_refunded_cents(payment) for payment in payments]
    return {
        "amount_paid_cents": sum(int(payment.amount_paid_cents) for payment in payments),
        "refunded_cents": sum(refunded),
        "refund_cents": sum(max(0, _target(payment, periods[payment.pk], now, contract.withdrawal_policy) - balance)
                            for payment, balance in zip(payments, refunded)),
        "currency": payments[0].currency if payments else contract.purchase.currency,
    }


def perform_object_withdrawal(declaration):
    from .contracts import _refund_row, _stop_future_charges, _stripe_refunded_cents, retry_refund

    contract = declaration.contract
    try:
        periods = _invoices(contract, declaration.received_at)
        _stop_future_charges(contract, declaration.received_at)
    except Exception as exc:
        Contract.objects.filter(pk=contract.pk).update(needs_review=True, review_reason="withdrawal_provider_failed")
        log.error("Object withdrawal preparation failed declaration=%s error=%s", declaration.pk, type(exc).__name__)
        return

    complete = True
    remaining = declaration.decision_refund_cents
    for payment in contract.payments.filter(pk__in=periods).order_by("paid_at", "pk"):
        try:
            refunded = _stripe_refunded_cents(payment)
            row = payment.refunds.filter(declaration=declaration).exclude(status__in=("succeeded", "cancelled")).first()
            if not row:
                payment.refunded_cents = max(payment.refunded_cents, refunded)
                payment.save(update_fields=("refunded_cents",))
            target = _target(payment, periods[payment.pk], declaration.received_at, contract.withdrawal_policy)
            if remaining is not None:
                target = min(payment.amount_paid_cents, remaining)
                remaining -= target
            amount = max(0, target - refunded)
            if not row:
                row = _refund_row(declaration, payment, refunded, amount)
            if row:
                row = retry_refund(row.pk)
                if row.status != "succeeded":
                    complete = False
            elif amount:
                complete = False
        except Exception as exc:
            complete = False
            Contract.objects.filter(pk=contract.pk).update(needs_review=True, review_reason="refund_balance_lookup_failed")
            log.error("Object refund failed declaration=%s payment=%s error=%s", declaration.pk, payment.pk, type(exc).__name__)
    if remaining:
        complete = False
    if complete:
        declaration.status = "effective"
        declaration.effective_at = declaration.received_at
        declaration.save(update_fields=("status", "effective_at"))
