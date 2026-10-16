"""Stripe operations for the opt-in billing app. Imports Stripe only at call time."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .config import load_billing_config
from .models import BillingCustomer, StripePrice, Subscription, TeamPackage
from .services import credit_purchase, grant_tokens


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
            if plan.kind == "subscription":
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


def checkout_session_params(*, plan, price, user, customer_id):
    """Build only parameters supported by Stripe Checkout SessionCreateParams in Stripe 16."""
    mode = "subscription" if plan.kind == "subscription" else "payment"
    params = {"mode": mode, "customer": customer_id, "line_items": [{"price": price.stripe_price_id, "quantity": 1}],
        "success_url": getattr(settings, "DCM_BILLING_SUCCESS_URL", ""),
        "cancel_url": getattr(settings, "DCM_BILLING_CANCEL_URL", ""),
        "metadata": {"app": getattr(settings, "DCM_BILLING_APP", ""), "plan_key": plan.key, "user_id": str(user.pk)},
        "billing_address_collection": "required", "automatic_tax": {"enabled": True},
        "managed_payments": {"enabled": True}}
    for key in ("seats", "term_months", "token_allowance", "tokens"):
        value = getattr(plan, key, None)
        if value:
            params["metadata"][key] = str(value)
    if mode == "subscription":
        params["subscription_data"] = {"metadata": {"app": getattr(settings, "DCM_BILLING_APP", ""),
            "plan_key": plan.key, "user_id": str(user.pk)}}
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


def _grant_period(user, plan, subscription_id, period_start, period_end):
    if not user or not plan or plan.token_allowance <= 0 or not period_end:
        return
    start_key = str(int(period_start or period_end))
    grant_tokens(user, plan.token_allowance, expires_at=_utc(period_end),
        reference=f"stripe:{subscription_id}:{start_key}", purpose=f"plan:{plan.key}")


def _upsert_subscription(obj, *, user=None):
    data = _dict(obj)
    metadata = data.get("metadata") or {}
    user = user or _user_from_metadata(metadata)
    sid = data.get("id")
    if not sid or not user:
        return None
    if metadata.get("app") != getattr(settings, "DCM_BILLING_APP", ""):
        return None  # the subscription itself must carry this app
    plan_key = metadata.get("plan_key") or ""
    status = data.get("status", "incomplete")
    start, end = _period(data)
    values = {"user": user, "plan_key": plan_key, "status": status,
        "current_period_start": _utc(start), "current_period_end": _utc(end),
        "cancel_at_period_end": bool(data.get("cancel_at_period_end")),
        "ended_at": _utc(data.get("canceled_at") or data.get("ended_at")) if status in ("canceled", "unpaid", "incomplete_expired") else None}
    sub, _ = Subscription.objects.update_or_create(stripe_subscription_id=sid, defaults=values)
    plan = _plan(plan_key)
    # No allowance for a period whose payment failed: past_due keeps access for now but grants nothing.
    if plan and status == "active":
        _grant_period(user, plan, sid, start, end)
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


def process_stripe_event(event):
    """Process one verified Stripe event. Caller owns the event-row transaction."""
    event = _dict(event)
    kind = event.get("type", "")
    obj = ((event.get("data") or {}).get("object") or {})
    obj = _dict(obj)
    metadata = _event_app_and_metadata(kind, obj)
    if metadata.get("app") != getattr(settings, "DCM_BILLING_APP", ""):
        return "ignored"
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        _upsert_subscription(obj)
    elif kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        plan = _plan(metadata.get("plan_key", ""))
        user = _user_from_metadata(metadata)
        if not plan or not user:
            return "ignored"
        if plan.kind == "subscription":
            sid = obj.get("subscription")
            if sid:
                _upsert_subscription(_stripe().Subscription.retrieve(sid), user=user)
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
        else:
            if obj.get("payment_status") in ("paid", "no_payment_required"):
                credit_purchase(user, _bought(metadata, "tokens", plan.tokens), reference=f"stripe:checkout:{obj.get('id')}")
    elif kind == "invoice.paid":
        sub_id = _invoice_subscription_id(obj)
        if sub_id:
            _upsert_subscription(_stripe().Subscription.retrieve(sub_id))
    elif kind == "invoice.payment_failed":
        sub_id = _invoice_subscription_id(obj)
        sub = Subscription.objects.filter(stripe_subscription_id=sub_id).first()
        if sub:
            sub.status = "past_due"
            sub.save(update_fields=("status",))
    else:
        return "ignored"
    return "processed"
