"""Stripe operations for the opt-in billing app. Imports Stripe only at call time."""
from __future__ import annotations

import hashlib
import json
import logging
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .config import load_billing_config
from .models import (BillingCustomer, StripePrice, Subscription, TeamPackage, Purchase, TokenEntry, CheckoutAttempt,
                     Contract, ContractPayment, Refund)
from .services import credit_purchase, grant_tokens

log = logging.getLogger(__name__)


def _stripe():
    try:
        import stripe
    except ImportError as exc:
        raise ImportError("Install django-core-micha[billing] to use Stripe billing") from exc
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
    return stripe


def stripe_fingerprint(plan, currency="usd"):
    fields = {"amount_cents": plan.amount_cents, "currency": currency, "interval": plan.interval,
              "kind": plan.kind, "tax_code": plan.tax_code}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def sync_plans(*, dry_run=False):
    """Ensure product and immutable versioned prices exist for every declared plan."""
    cfg = load_billing_config()
    if dry_run:
        return [(p.key, "would_sync") for p in cfg.plans]
    stripe = _stripe()
    app = cfg.app
    result = []
    for plan in cfg.plans:
        fp = stripe_fingerprint(plan, cfg.currency)
        current = StripePrice.objects.filter(app=app, plan_key=plan.key).order_by("-version").first()
        if current and current.fingerprint == fp:
            result.append((plan.key, "unchanged"))
            continue
        product_id = current.stripe_product_id if current else ""
        if not product_id:
            products = stripe.Product.list(limit=100)
            for product in products.auto_paging_iter():
                metadata = _dict(getattr(product, "metadata", {}))
                if metadata.get("app") == app and metadata.get("plan_key") == plan.key:
                    product_id = product.id
                    break
        if not product_id:
            product = stripe.Product.create(name=plan.name, tax_code=plan.tax_code,
                metadata={"app": app, "plan_key": plan.key})
            product_id = product.id
        if current and product_id:
            remote_product = _dict(stripe.Product.retrieve(product_id))
            existing_tax_code = remote_product.get("tax_code") if isinstance(remote_product, dict) else getattr(remote_product, "tax_code", None)
            if existing_tax_code and existing_tax_code != plan.tax_code:
                product_id = stripe.Product.create(name=plan.name, tax_code=plan.tax_code,
                    metadata={"app": app, "plan_key": plan.key}).id
            else:
                stripe.Product.modify(product_id, name=plan.name)
        version = (current.version + 1) if current else 1
        lookup_key = f"{app}:{plan.key}:v{version}"
        remote = list(stripe.Price.list(lookup_keys=[lookup_key], limit=1).data)
        if remote:
            price = remote[0]
        else:
            args = {"product": product_id, "unit_amount": plan.amount_cents, "currency": cfg.currency,
                    "lookup_key": lookup_key, "tax_behavior": "exclusive",
                    "metadata": {"app": app, "plan_key": plan.key, "version": str(version)}}
            if plan.kind in ("subscription", "continuation"):
                args["recurring"] = {"interval": plan.interval}
            price = stripe.Price.create(**args)
        StripePrice.objects.create(app=app, plan_key=plan.key, version=version, lookup_key=lookup_key,
            fingerprint=fp, stripe_product_id=product_id, stripe_price_id=price.id)
        result.append((plan.key, f"v{version}"))
    return result


def _dict(value):
    if hasattr(value, "to_dict_recursive"):
        value = value.to_dict_recursive()
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {k: _dict(v) if isinstance(v, dict) or hasattr(v, "to_dict_recursive") else v for k, v in value.items()}
    return value


def _latest_price(plan_key):
    cfg = load_billing_config()
    row = StripePrice.objects.filter(app=cfg.app, plan_key=plan_key).order_by("-version").first()
    if row is None:
        raise LookupError("plan_not_synced")
    return row


def get_customer(user):
    row = BillingCustomer.objects.filter(user=user).first()
    if row:
        return row
    stripe = _stripe()
    app = getattr(settings, "DCM_BILLING_APP", "")
    # the idempotency key makes two simultaneous first checkouts of one user resolve to the same Stripe customer
    customer = stripe.Customer.create(email=getattr(user, "email", "") or None,
        metadata={"app": app, "user_id": str(user.pk)}, idempotency_key=f"dcm-customer:{app}:{user.pk}")
    row, _ = BillingCustomer.objects.get_or_create(user=user, defaults={"stripe_customer_id": customer.id})
    return row


def checkout_session_params(*, plan, price, user, customer_id, success_url=None, cancel_url=None, attempt_reference=None, context=None):
    """Build only parameters supported by Stripe Checkout SessionCreateParams in Stripe 16."""
    if plan.kind == "continuation" or not getattr(plan, "available", True):
        raise LookupError("unknown_plan")
    mode = "subscription" if plan.kind == "subscription" else "payment"
    params = {"mode": mode, "customer": customer_id, "line_items": [{"price": price.stripe_price_id, "quantity": 1}],
        "success_url": success_url if success_url is not None else getattr(settings, "DCM_BILLING_SUCCESS_URL", ""),
        "cancel_url": cancel_url if cancel_url is not None else getattr(settings, "DCM_BILLING_CANCEL_URL", ""),
        "metadata": {"app": getattr(settings, "DCM_BILLING_APP", ""), "plan_key": plan.key, "user_id": str(user.pk)},
        "billing_address_collection": "required",
        "managed_payments": {"enabled": True}}
    policy=load_billing_config().contract
    if policy: params["metadata"]["terms_version"]=policy["terms_version"]
    if getattr(settings, "DCM_BILLING_AUTOMATIC_TAX", False) is True:  # a real boolean only: the string "False" must not switch it on
        params["automatic_tax"] = {"enabled": True}
    for key in ("seats", "term_months", "token_allowance", "tokens"):
        value = getattr(plan, key, None)
        if value:
            params["metadata"][key] = str(value)
    if attempt_reference is not None:
        params["client_reference_id"] = attempt_reference
        params["metadata"]["checkout_attempt"] = attempt_reference
        params["metadata"]["checkout_context"] = context
    if mode == "subscription":
        params["subscription_data"] = {"metadata": {"app": getattr(settings, "DCM_BILLING_APP", ""),
            "plan_key": plan.key, "user_id": str(user.pk)}}
        if policy:
            params["subscription_data"]["metadata"]["terms_version"]=policy["terms_version"]
    else:
        params["payment_intent_data"] = {"metadata": params["metadata"]}
    return params


def _period(data):
    """Billing period of a subscription: top level (older API versions) or on its first item (newer versions)."""
    start, end = data.get("current_period_start"), data.get("current_period_end")
    if start is None or end is None:
        items = ((data.get("items") or {}).get("data") or [])
        if items and isinstance(items[0], dict):
            start = items[0].get("current_period_start") if start is None else start
            end = items[0].get("current_period_end") if end is None else end
    return start, end


def _invoice_subscription_id(invoice):
    """Subscription of an invoice: `subscription` (older API versions) or `parent.subscription_details` (newer)."""
    sub_id = invoice.get("subscription")
    if not sub_id:
        sub_id = (((invoice.get("parent") or {}).get("subscription_details") or {}).get("subscription"))
    if isinstance(sub_id, dict):
        sub_id = sub_id.get("id")
    return sub_id


def _invoice_payment_intent(invoice):
    """Payment intent of a paid invoice: `payment_intent` (older API versions) or its InvoicePayment (2025+ versions,
    where the invoice itself no longer carries it; measured on 2026-10-28.endive)."""
    invoice = _dict(invoice or {})
    value = invoice.get("payment_intent")
    if isinstance(value, dict):
        value = value.get("id")
    if value:
        return str(value)
    payments = (invoice.get("payments") or {}).get("data") if isinstance(invoice.get("payments"), dict) else None
    if payments is None and invoice.get("id"):
        try:
            payments = getattr(_stripe().InvoicePayment.list(invoice=invoice["id"], limit=10), "data", [])
        except Exception as exc:
            log.error("Invoice payment lookup failed (%s)", type(exc).__name__)
            payments = []
    for item in payments or []:
        item = _dict(item)
        if item.get("status") not in (None, "paid"):
            continue
        payment = _dict(item.get("payment") or {})
        intent = payment.get("payment_intent")
        if isinstance(intent, dict):
            intent = intent.get("id")
        if intent:
            return str(intent)
    return ""


def _utc(timestamp):
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=dt_timezone.utc)


def _user_from_metadata(metadata):
    user_id = _dict(metadata or {}).get("user_id")
    if not user_id:
        customer_id = None
        return None
    return get_user_model().objects.filter(pk=user_id).first()


def _bought(metadata, key, fallback):
    """The entitlement fixed at checkout (session metadata), falling back to the current plan."""
    try:
        value = int((metadata or {}).get(key) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else fallback


def _plan(plan_key):
    return next((p for p in load_billing_config().plans if p.key == plan_key), None)


def _contract_policy():
    """The app's `contract:` block, or None when the app has none (or, in a bare event path, no billing.yaml at all)."""
    try:
        return load_billing_config().contract
    except FileNotFoundError:
        return None


def _grant_period(user, plan, subscription_id, period_start, period_end):
    if not user or not plan or plan.token_allowance <= 0 or not period_end:
        return
    start_key = str(int(period_start or period_end))
    grant_tokens(user, plan.token_allowance, expires_at=_utc(period_end),
        reference=f"stripe:{subscription_id}:{start_key}", purpose=f"plan:{plan.key}")


def _upsert_subscription(obj, *, user=None, allow_grant=True):
    data = _dict(obj)
    metadata = data.get("metadata") or {}
    user = user or _user_from_metadata(metadata)
    sid = data.get("id")
    if not sid or not user:
        return None
    if metadata.get("app") != getattr(settings, "DCM_BILLING_APP", ""):
        return None  # the subscription itself must carry this app
    # the row lock waits for a running withdrawal, so a concurrent event never regrants after it
    if Purchase.objects.select_for_update().filter(stripe_object_id=sid, status="withdrawn").exists():
        return Subscription.objects.filter(stripe_subscription_id=sid).first()
    items=((data.get("items") or {}).get("data") or [])
    price_id=""
    if items:
        price=items[0].get("price") or {}
        price_id=price.get("id") if isinstance(price,dict) else price
    price_row=StripePrice.objects.filter(app=getattr(settings,"DCM_BILLING_APP",""),stripe_price_id=price_id).first() if price_id else None
    plan_key=(price_row.plan_key if price_row else "") or metadata.get("plan_key") or ""
    status = data.get("status", "incomplete")
    start, end = _period(data)
    contract=Contract.objects.filter(stripe_object_id=sid,app=getattr(settings,"DCM_BILLING_APP","")).first()
    if contract and contract.status in ("withdrawn","ended"):
        return Subscription.objects.filter(stripe_subscription_id=sid).first()
    if contract and contract.status=="cancellation_scheduled" and contract.cancel_effective_at and _utc(start) and _utc(start)>=contract.cancel_effective_at:
        return Subscription.objects.filter(stripe_subscription_id=sid).first()
    cancel_at_period_end=bool(data.get("cancel_at_period_end"))
    if contract and contract.status=="cancellation_scheduled" and contract.cancel_effective_at and _utc(end)==contract.cancel_effective_at:
        cancel_at_period_end=True
    values = {"user": user, "plan_key": plan_key, "status": status,
        "current_period_start": _utc(start), "current_period_end": _utc(end),
        "cancel_at_period_end": cancel_at_period_end,
        "ended_at": _utc(data.get("ended_at") or data.get("canceled_at")) if status in ("canceled", "unpaid", "incomplete_expired") else None}
    sub, _ = Subscription.objects.update_or_create(stripe_subscription_id=sid, defaults=values)
    if contract and status in ("canceled","incomplete_expired") and contract.status=="cancellation_scheduled":
        ended=_utc(data.get("ended_at") or data.get("canceled_at")) or timezone.now()
        contract.status="ended"; contract.save(update_fields=("status","updated_at"))
        for declaration in Declaration.objects.filter(contract=contract,kind="cancellation",status="scheduled"):
            declaration.status="effective"; declaration.effective_at=ended; declaration.save(update_fields=("status","effective_at"))
    plan = _plan(plan_key)
    if contract and contract.continuation_plan_key==plan_key and contract.continuation_token_allowance is not None:
        plan=SimpleNamespace(key=plan_key,kind="continuation",token_allowance=contract.continuation_token_allowance,
            amount_cents=contract.continuation_amount_cents or 0,interval=contract.continuation_interval)
    # No allowance for a period whose payment failed: past_due keeps access for now but grants nothing.
    if plan and status == "active" and allow_grant:
        _grant_period(user, plan, sid, start, end)
    if contract and plan and plan.kind=="continuation":
        Contract.objects.filter(pk=contract.pk).update(phase="continuation")
    return sub


def _add_months(value, months):
    from calendar import monthrange
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def _event_app_and_metadata(event_type, obj):
    data = _dict(obj)
    metadata = data.get("metadata") or {}
    if event_type.startswith("invoice."):
        if metadata.get("app"):
            return metadata
        lines = data.get("lines") or {}
        line_data = lines.get("data", []) if isinstance(lines, dict) else []
        for line in line_data:
            meta = line.get("metadata") or {}
            if meta.get("app"):
                return meta
        sub_id = _invoice_subscription_id(data)
        if sub_id:
            sub = _dict(_stripe().Subscription.retrieve(sub_id))
            return sub.get("metadata") or {}
    return metadata


def _invoice_plan_key(invoice):
    for line in ((invoice.get("lines") or {}).get("data") or []):
        price=line.get("price") or (((line.get("pricing") or {}).get("price_details") or {}).get("price"))
        if isinstance(price,dict): price=price.get("id")
        row=StripePrice.objects.filter(app=getattr(settings,"DCM_BILLING_APP",""),stripe_price_id=price).first() if price else None
        if row: return row.plan_key
    return ""


def _record_contract_payment(contract,invoice,billing_reason):
    invoice_id=invoice.get("id") or ""
    if invoice_id and ContractPayment.objects.filter(stripe_invoice_id=invoice_id).exists(): return ContractPayment.objects.get(stripe_invoice_id=invoice_id)
    payment_intent=_invoice_payment_intent(invoice)
    charge=invoice.get("charge") or ""
    if isinstance(charge,dict): charge=charge.get("id") or ""
    try: paid=max(0,int(invoice.get("amount_paid") or 0))
    except (TypeError,ValueError): paid=0
    timestamp=((invoice.get("status_transitions") or {}).get("paid_at") or invoice.get("created"))
    return ContractPayment.objects.create(contract=contract,stripe_invoice_id=invoice_id,
        stripe_payment_intent_id=str(payment_intent),stripe_charge_id=str(charge),amount_paid_cents=paid,
        currency=(invoice.get("currency") or "usd").lower(),paid_at=_utc(timestamp) or timezone.now(),billing_reason=billing_reason)


def _withdrawal_purchase_pk(app, *candidates):
    """The purchase id out of our withdrawal key `dcm-withdrawal:<app>:<id>` (metadata or request idempotency key)."""
    prefix = f"dcm-withdrawal:{app}:"
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(prefix):
            tail = candidate[len(prefix):]
            if tail.isascii() and tail.isdigit() and 0 < len(tail) <= 18 and int(tail) > 0:
                return int(tail)
    return None


def withdrawal_refund_purchase(refund, idempotency_key=None):
    """Return the active purchase only for a validated withdrawal refund.

    A refund is ours only through the withdrawal key: the refund metadata `dcm_withdrawal`, or the idempotency
    key of the request that created it (carried by the event; it is how refunds created before the metadata key existed are found).
    Manual dashboard refunds and disputes carry neither."""
    refund = _dict(refund)
    app = getattr(settings, "DCM_BILLING_APP", "")
    metadata = _dict(refund.get("metadata") or {})
    purchase_pk = _withdrawal_purchase_pk(app, metadata.get("dcm_withdrawal"), idempotency_key)
    if purchase_pk is None:
        return None
    if refund.get("status") != "succeeded":
        return None
    purchase = Purchase.objects.filter(pk=purchase_pk, status="active").first()
    if not purchase:
        return None
    if metadata.get("app") not in (None, "", app):
        return None
    payment_intent = refund.get("payment_intent")
    if isinstance(payment_intent, dict):
        payment_intent = payment_intent.get("id")
    if not purchase.stripe_payment_intent_id or payment_intent != purchase.stripe_payment_intent_id:
        return None
    amount = refund.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool):
        return None  # integer cents exactly as Stripe sends them, never truncated
    if amount <= 0 or amount > int(purchase.amount_paid_cents):
        return None
    currency = refund.get("currency")
    if not currency or str(currency).lower() != str(purchase.currency).lower():
        return None
    return purchase


def complete_withdrawal_from_refund(refund, idempotency_key=None):
    refund = _dict(refund)
    purchase = withdrawal_refund_purchase(refund, idempotency_key)
    if purchase is None:
        return False
    from .withdrawal import complete_withdrawal
    created = refund.get("created")
    now = _utc(created) if created else None
    now = now or timezone.now()
    currency = (refund.get("currency") or purchase.currency).lower()
    amount = int(refund["amount"])
    note = f"Completed from the Stripe refund event; refund: {amount} {currency} cents."
    complete_withdrawal(purchase.pk, now=now, refund_cents=amount,
        refund_id=refund.get("id") or "", note=note, event=True)
    return True


def complete_contract_refund(refund, idempotency_key=None, event_kind="refund.updated"):
    data=_dict(refund); metadata=data.get("metadata") or {}; app=getattr(settings,"DCM_BILLING_APP","")
    key=metadata.get("dcm_refund") or idempotency_key
    if not isinstance(key,str) or not key.startswith(f"dcm-refund:{app}:"): return False
    row=Refund.objects.select_for_update().select_related("payment","contract").filter(idempotency_key=key,contract__app=app).first()
    if not row or metadata.get("app")!=app: return False
    if metadata.get("contract_id") not in (None,"",str(row.contract_id)): return False
    payment_intent=data.get("payment_intent")
    if isinstance(payment_intent,dict): payment_intent=payment_intent.get("id")
    if payment_intent != row.payment.stripe_payment_intent_id: return False
    amount=data.get("amount")
    if not isinstance(amount,int) or isinstance(amount,bool) or amount<=0 or amount>row.amount_cents: return False
    currency=data.get("currency")
    if not currency or str(currency).lower()!=row.currency.lower(): return False
    if row.status=="succeeded": return True
    provider_status=data.get("status") or ("failed" if event_kind=="refund.failed" else "pending")
    next_status={"succeeded":"succeeded","pending":"requested","failed":"failed","requires_action":"requires_action"}.get(provider_status,"requested")
    if row.status in ("failed","cancelled") and next_status in ("requested","requires_action"): return True
    if next_status=="succeeded":
        row.payment.refunded_cents=int(row.payment.refunded_cents or 0)+amount
        row.payment.save(update_fields=("refunded_cents",))
    row.status=next_status; row.stripe_refund_id=data.get("id") or row.stripe_refund_id
    row.last_error_code="" if next_status!="failed" else event_kind
    row.save(update_fields=("status","stripe_refund_id","last_error_code","updated_at"))
    return True


def process_stripe_event(event):
    """Process one verified Stripe event. Caller owns the event-row transaction."""
    event = _dict(event)
    kind = event.get("type", "")
    obj = ((event.get("data") or {}).get("object") or {})
    obj = _dict(obj)
    if kind in ("refund.created", "refund.updated", "refund.failed"):
        request_info = event.get("request")
        request_key = _dict(request_info).get("idempotency_key") if isinstance(request_info, dict) else None
        complete_contract_refund(obj,request_key,kind)
        complete_withdrawal_from_refund(obj, request_key)
        return "processed"
    if kind == "charge.refunded":
        payment_intent = obj.get("payment_intent")
        if isinstance(payment_intent, dict):
            payment_intent = payment_intent.get("id")
        active = Purchase.objects.filter(status="active", stripe_payment_intent_id=payment_intent).exists() if payment_intent else False
        contract_refunds=Refund.objects.filter(payment__stripe_payment_intent_id=payment_intent).exclude(status__in=("succeeded","cancelled")).exists() if payment_intent else False
        if active or contract_refunds:
            refunds = _stripe().Refund.list(charge=obj.get("id"), limit=100)
            for refund in getattr(refunds, "data", refunds if isinstance(refunds, list) else []):
                complete_contract_refund(refund)
                complete_withdrawal_from_refund(refund)
        return "processed"
    metadata = _event_app_and_metadata(kind, obj)
    if metadata.get("app") != getattr(settings, "DCM_BILLING_APP", ""):
        return "ignored"
    if kind in ("checkout.session.async_payment_failed", "checkout.session.expired"):
        return "processed" if _update_checkout_attempt(kind, obj, metadata) else "ignored"
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        contract=Contract.objects.filter(stripe_object_id=obj.get("id"),app=getattr(settings,"DCM_BILLING_APP","")).first()
        if contract:
            allow_grant=not bool(contract.terms_version)
        else:
            allow_grant=not bool((metadata.get("terms_version") or _contract_policy()))
        _upsert_subscription(obj,allow_grant=allow_grant)
    elif kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        plan = _plan(metadata.get("plan_key", ""))
        user = _user_from_metadata(metadata)
        if not plan or not user:
            return "ignored"
        # in the caller's transaction: a grant below that raises rolls the attempt status back with it
        _update_checkout_attempt(kind, obj, metadata)
        if plan.kind == "subscription":
            sid = obj.get("subscription")
            if sid:
                remote = _dict(_stripe().Subscription.retrieve(sid))
                paid=obj.get("payment_status") in ("paid","no_payment_required")
                opted=bool((remote.get("metadata") or {}).get("terms_version") or _contract_policy())
                sub = _upsert_subscription(remote, user=user,allow_grant=paid or not opted)
                inv = remote.get("latest_invoice") or {}
                if isinstance(inv, str):
                    try: inv = _dict(_stripe().Invoice.retrieve(inv))
                    except Exception: inv = {}
                if paid:  # a delayed payment creates it on the success event
                  _create_purchase(obj, event, user, plan, "subscription", sid,
                    payment_intent=(_invoice_payment_intent(inv) if inv else "") or obj.get("payment_intent"),
                    invoice_id=inv.get("id") or "", term_start=_utc(_period(remote)[0]), term_end=_utc(_period(remote)[1]),
                    grant_refs=[f"stripe:{sid}:{int(_period(remote)[0] or _period(remote)[1] or 0)}"] if _period(remote)[1] else [], tokens=plan.token_allowance, invoice_data=inv)
        elif plan.kind == "team_package":
            if obj.get("payment_status") not in ("paid", "no_payment_required"):
                return "ignored"  # seats and tokens only after the payment has succeeded
            ref = obj.get("payment_intent") or obj.get("id")
            now = timezone.now()
            starts = _utc(event.get("created")) or _utc(obj.get("created")) or now
            seats = _bought(metadata, "seats", plan.seats)
            allowance = _bought(metadata, "token_allowance", plan.token_allowance)
            ends = _add_months(starts, _bought(metadata, "term_months", plan.term_months))
            package, created = TeamPackage.objects.get_or_create(stripe_ref=str(ref), defaults={
                "owner": user, "plan_key": plan.key, "seats": seats, "starts_at": starts, "ends_at": ends})
            if created and allowance:
                grant_tokens(package, allowance, expires_at=ends,
                    reference=f"stripe:team:{ref}", purpose=f"plan:{plan.key}")
            _create_purchase(obj,event,user,plan,"team_package",str(ref),payment_intent=obj.get("payment_intent"),term_start=starts,term_end=ends,grant_refs=[f"stripe:team:{ref}"] if allowance else [],tokens=allowance)
        else:
            if obj.get("payment_status") in ("paid", "no_payment_required"):
                ref=f"stripe:checkout:{obj.get('id')}"
                tokens=_bought(metadata, "tokens", plan.tokens)
                credit_purchase(user, tokens, reference=ref)
                _create_purchase(obj,event,user,plan,"token_package",str(obj.get("payment_intent") or obj.get("id")),payment_intent=obj.get("payment_intent"),grant_refs=[ref],tokens=tokens)
    elif kind == "invoice.paid":
        sub_id = _invoice_subscription_id(obj)
        if sub_id:
            sub=_upsert_subscription(_stripe().Subscription.retrieve(sub_id),allow_grant=True)
            contract=Contract.objects.filter(stripe_object_id=sub_id,app=getattr(settings,"DCM_BILLING_APP","")).first()
            if contract and contract.status not in ("withdrawn","ended"):
                invoice_start=(obj.get("period_start") or next((line.get("period",{}).get("start") for line in ((obj.get("lines") or {}).get("data") or []) if line.get("period",{}).get("start")),None))
                if contract.cancel_effective_at and invoice_start and _utc(invoice_start)>=contract.cancel_effective_at:
                    return "processed"
                plan_key=_invoice_plan_key(obj) or (sub.plan_key if sub else "")
                plan=_plan(plan_key); is_continuation=bool(contract.continuation_plan_key and contract.continuation_plan_key==plan_key)
                reason="continuation" if is_continuation or (plan and plan.kind=="continuation") else ("initial" if obj.get("billing_reason")=="subscription_create" else "renewal")
                _record_contract_payment(contract,obj,reason)
    elif kind == "invoice.payment_failed":
        sub_id = _invoice_subscription_id(obj)
        sub = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
        if sub:
            sub.status = "past_due"
            sub.save(update_fields=("status",))
    else:
        return "ignored"
    return "processed"

def _update_checkout_attempt(kind, obj, metadata):
    transitions={"checkout.session.completed": "open",
                 "checkout.session.async_payment_succeeded": "paid",
                 "checkout.session.async_payment_failed": "failed",
                 "checkout.session.expired": "expired"}
    if kind not in transitions: return False
    session_id=obj.get("id") or ""
    qs=CheckoutAttempt.objects.select_for_update()
    attempt=qs.filter(stripe_session_id=session_id).first() if session_id else None
    if attempt is None:
        reference=(metadata or {}).get("checkout_attempt")
        user_id=(metadata or {}).get("user_id")
        if not reference or not user_id: return False
        # only an attempt whose session was never stored: a bound attempt belongs to its own session
        attempt=qs.filter(reference=reference,app=getattr(settings,"DCM_BILLING_APP",""),user_id=user_id,stripe_session_id="").first()
        if attempt is None: return False
    metadata=metadata or {}
    if (str(metadata.get("user_id") or "")!=str(attempt.user_id) or metadata.get("plan_key")!=attempt.plan_key
            or metadata.get("checkout_context") not in (None,attempt.context)):
        return False  # a session for another person, plan or context never moves this attempt
    target=transitions[kind]
    if kind=="checkout.session.completed":
        target="paid" if obj.get("payment_status") in ("paid","no_payment_required") else ("processing" if obj.get("payment_status")=="unpaid" else "open")
    allowed={"open": {"processing","paid","failed","expired"}, "processing":{"paid","failed"}}
    transitioned=target in allowed.get(attempt.status,set())
    if transitioned:
        attempt.status=target
    bound=bool(session_id and not attempt.stripe_session_id)
    if bound: attempt.stripe_session_id=session_id
    if transitioned or bound:
        attempt.save(update_fields=("status","stripe_session_id","updated_at"))
    return transitioned

def _create_purchase(obj,event,user,plan,kind,object_id,*,payment_intent=None,invoice_id="",term_start=None,term_end=None,grant_refs=None,tokens=0,invoice_data=None):
    details=obj.get("customer_details") or {}
    address=details.get("address") or {}
    tax_ids=details.get("tax_ids") or []
    created=_utc(event.get("created")) or _utc(obj.get("created")) or timezone.now()
    try: amount=max(0,int(obj["amount_total"])) if obj.get("amount_total") is not None else int(plan.amount_cents)
    except (TypeError,ValueError): amount=int(plan.amount_cents)
    existing=Purchase.objects.filter(stripe_object_id=str(object_id)).first()
    if existing and not existing.stripe_payment_intent_id and payment_intent and existing.status=="active":
        existing.stripe_payment_intent_id=str(payment_intent); existing.save(update_fields=("stripe_payment_intent_id",))
    purchase=Purchase.objects.get_or_create(stripe_object_id=str(object_id),defaults={
        "user":user,"kind":kind,"plan_key":plan.key,"stripe_payment_intent_id":str(payment_intent or ""),"stripe_invoice_id":str(invoice_id or ""),
        "amount_paid_cents":amount,"currency":(obj.get("currency") or load_billing_config().currency).lower(),"billing_country":(address.get("country") or "").upper(),
        "has_business_tax_id":bool(tax_ids),"purchased_at":created,"term_start":term_start,"term_end":term_end,
        "grant_references":grant_refs or [],"tokens_granted":max(0,int(tokens or 0))})[0]
    cfg=load_billing_config(); terms=(obj.get("metadata") or {}).get("terms_version")
    from .contracts import create_contract_for_purchase
    contract,_=create_contract_for_purchase(purchase,plan,info_delivered=bool(cfg.contract and terms==cfg.contract["terms_version"]))
    if invoice_data is None: invoice_data={}
    invoice_data=_dict(invoice_data)
    if not invoice_data.get("amount_paid"):
        invoice_data["amount_paid"]=amount
    if not invoice_data.get("id"):
        invoice_data["id"]=str(invoice_id or "")
    if not invoice_data.get("payment_intent"):
        invoice_data["payment_intent"]=payment_intent or ""
    if not invoice_data.get("currency"):
        invoice_data["currency"]=(obj.get("currency") or cfg.currency).lower()
    if not invoice_data.get("created"):
        invoice_data["created"]=int(created.timestamp())
    if invoice_id and ContractPayment.objects.filter(stripe_invoice_id=str(invoice_id)).exists():
        pass
    elif not invoice_id and ContractPayment.objects.filter(contract=contract,stripe_invoice_id="",billing_reason="initial").exists():
        pass
    else:
        _record_contract_payment(contract,invoice_data,"initial")
    return purchase
