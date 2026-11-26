"""App-authorized object checkout and its provider-confirmed attempt state.

The app must supply an authorization callable that returns True only after checking
both object existence and the buyer's permission. Lifecycle consumers must lock the
subject before changing a blocking attempt or subscription.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from .config import load_billing_config
from .models import ObjectBillingSubject, ObjectCheckoutAttempt
from .stripe_service import _latest_price, _stripe, checkout_session_params
from .object_portal import object_customer


class ObjectCheckoutConflict(Exception):
    pass


def start_object_checkout(*, user, object_key, plan_key, idempotency_key, authorize,
                          success_url, cancel_url):
    """Return an attempt; callers must use trusted, server-configured return URLs.

    No browser return changes billing state. Ambiguous provider failures stay reserved.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        raise PermissionError("authentication_required")
    if not isinstance(object_key, str) or not object_key or len(object_key) > 255:
        raise PermissionError("object_not_authorized")
    if not callable(authorize) or authorize(user, object_key) is not True:
        raise PermissionError("object_not_authorized")
    if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", idempotency_key, flags=re.ASCII):
        raise ValueError("invalid_idempotency_key")
    cfg = load_billing_config()
    plan = next ( (p for p in cfg.plans if p.key == plan_key and p.kind == "object_subscription" and p.available), None)
    if plan is None:
        raise LookupError("unknown_plan")
    subject, _ = ObjectBillingSubject.objects.get_or_create(app=cfg.app, object_key=object_key)
    # The reservation must commit independently before any provider request.
    # Reject callers that enclose checkout in another database transaction.
    with transaction.atomic(durable=True):
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=subject.pk)
        # Ended terms no longer reserve the object; the subject serializes this with events.
        for sub in subject.subscriptions.filter(blocks_purchase=True, status__in=("canceled", "incomplete_expired"), current_period_end__lte=timezone.now()):
            sub.blocks_purchase = False
            sub.save(update_fields=("blocks_purchase",))
        attempt = subject.attempts.filter(payer=user, idempotency_key=idempotency_key).first()
        if attempt:
            if attempt.plan_key != plan.key:
                raise ObjectCheckoutConflict("idempotency_key_reused")
            if attempt.status != "open":
                raise ObjectCheckoutConflict("attempt_not_open")
        else:
            if subject.subscriptions.filter(blocks_purchase=True).exists() or subject.attempts.filter(status__in=("open", "processing", "paid")).exists():
                raise ObjectCheckoutConflict("object_purchase_blocked")
            # Older versions could release attempts on a stale event. Recheck every
            # released session before allowing a new buyer to reach Stripe.
            for previous in subject.attempts.filter(status__in=("failed", "expired")):
                if not previous.stripe_session_id or not _terminal(previous):
                    raise ObjectCheckoutConflict("previous_attempt_unconfirmed")
            price = _latest_price(plan.key)
            attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=user,
                reference=secrets.token_urlsafe(32), plan_key=plan.key,
                idempotency_key=idempotency_key, stripe_price_id=price.stripe_price_id,
                isolated_customer_flow=True)
    # Commit the isolated customer before creating a session: a timeout must not
    # erase the identity needed to authenticate a later webhook.
    with transaction.atomic(durable=True):
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=subject.pk)
        attempt = subject.attempts.get(pk=attempt.pk)
        if attempt.status != "open":
            raise ObjectCheckoutConflict("attempt_not_open")
        if not attempt.stripe_customer_id:
            object_customer(attempt)
    # Serialize retries and use the same Stripe idempotency key for the session.
    with transaction.atomic():
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=subject.pk)
        attempt = subject.attempts.get(pk=attempt.pk)
        if attempt.status != "open":
            raise ObjectCheckoutConflict("attempt_not_open")
        if attempt.stripe_session_id:
            return attempt
        # Stripe may prune idempotency keys after 24 hours. Never resend an
        # ambiguous create after that window, even if no session ID was stored.
        if attempt.created_at <= timezone.now() - timedelta(hours=23):
            raise ObjectCheckoutConflict("session_creation_unconfirmed")
        price = type("StoredPrice", (), {"stripe_price_id": attempt.stripe_price_id})()
        params = checkout_session_params(plan=plan, price=price, user=user,
            customer_id=attempt.stripe_customer_id, success_url=success_url, cancel_url=cancel_url)
        params["metadata"]["object_checkout_attempt"] = attempt.reference
        params["subscription_data"]["metadata"]["object_checkout_attempt"] = attempt.reference
        # Even a 400 can be an idempotency parameter mismatch after a concurrent
        # request created the session. Never release on an ambiguous response.
        session = _stripe().checkout.Session.create(**params, idempotency_key=f"dcm-object-checkout:{cfg.app}:{attempt.reference}")
        attempt.stripe_session_id = session.id
        attempt.stripe_checkout_url = session.url
        expiry = getattr(session, "expires_at", None)
        attempt.expires_at = datetime.fromtimestamp(expiry, tz=dt_timezone.utc) if expiry else None
        attempt.save(update_fields=("stripe_session_id", "stripe_checkout_url", "expires_at", "updated_at"))
    return attempt


def update_object_attempt(kind, obj, metadata):
    """Apply a verified session event. Never unblock a pending delayed payment."""
    targets = {"checkout.session.completed": "processing",
               "checkout.session.async_payment_succeeded": "paid",
               "checkout.session.async_payment_failed": "failed",
               "checkout.session.expired": "expired"}
    if kind not in targets:
        return False
    attempt = ObjectCheckoutAttempt.objects.filter(reference=metadata.get("object_checkout_attempt"), subject__app=metadata.get("app")).first()
    if not attempt:
        return False
    with transaction.atomic():
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=attempt.subject_id)
        attempt = subject.attempts.get(pk=attempt.pk)
        session_id = obj.get("id") or ""
        if (not session_id or (attempt.stripe_session_id and attempt.stripe_session_id != session_id)
                or str(attempt.payer_id) != str(metadata.get("user_id")) or attempt.plan_key != metadata.get("plan_key")
                or not attempt.stripe_customer_id or obj.get("customer") != attempt.stripe_customer_id):
            return False
        target = targets[kind]
        if target in ("failed", "expired"):
            # An event can be delayed or reordered; only current provider state
            # can prove that neither Checkout nor its subscription can charge.
            if not _terminal(attempt, session_id):
                return False
        if kind == "checkout.session.completed" and obj.get("payment_status") in ("paid", "no_payment_required"):
            target = "paid"
        allowed = {"open": {"processing", "paid", "failed", "expired"}, "processing": {"paid", "failed", "expired"}}
        if target not in allowed.get(attempt.status, set()):
            return False
        attempt.status = target
        attempt.stripe_session_id = session_id
        attempt.save(update_fields=("status", "stripe_session_id", "updated_at"))
        return True


def _terminal(attempt, session_id=None):
    """Prove terminality from current Stripe state; exceptions leave it reserved."""
    from .stripe_service import _dict
    provider = _stripe()
    session = _dict(provider.checkout.Session.retrieve(session_id or attempt.stripe_session_id))
    meta = session.get("metadata") or {}
    if (session.get("id") != (session_id or attempt.stripe_session_id)
            or meta.get("object_checkout_attempt") != attempt.reference
            or meta.get("app") != attempt.subject.app
            or str(meta.get("user_id")) != str(attempt.payer_id)
            or meta.get("plan_key") != attempt.plan_key):
        return False
    recovery = (session.get("after_expiration") or {}).get("recovery") or {}
    if recovery.get("enabled") is not None and recovery.get("enabled") is not False:
        return False
    subscription = session.get("subscription")
    if (session.get("status") == "expired" and session.get("payment_status") == "unpaid"
            and not subscription):
        return True
    if session.get("status") != "complete" or session.get("payment_status") != "unpaid" or not subscription:
        return False
    sid = subscription.get("id") if isinstance(subscription, dict) else subscription
    remote = _dict(provider.Subscription.retrieve(sid))
    if remote.get("id") != sid or remote.get("status") != "incomplete_expired":
        return False
    invoice_id = remote.get("latest_invoice")
    if isinstance(invoice_id, dict):
        invoice_id = invoice_id.get("id")
    if not invoice_id:
        return False
    invoice = _dict(provider.Invoice.retrieve(invoice_id))
    if invoice.get("id") != invoice_id or invoice.get("status") != "void" or "payment_intent" not in invoice:
        return False
    intent = invoice["payment_intent"]
    if not intent:
        return True
    intent_id = intent.get("id") if isinstance(intent, dict) else intent
    intent = _dict(provider.PaymentIntent.retrieve(intent_id))
    return intent.get("id") == intent_id and intent.get("status") == "canceled"


def reconcile_object_attempt(attempt_id):
    """Recheck an unresolved session without trusting event delivery order."""
    attempt = ObjectCheckoutAttempt.objects.select_related("subject").get(pk=attempt_id)
    if not attempt.stripe_session_id:
        # A lost create response cannot safely be retried after Stripe's
        # idempotency retention. Recover only a uniquely identified session;
        # an empty or ambiguous list never proves that creation failed.
        with transaction.atomic():
            subject = ObjectBillingSubject.objects.select_for_update().get(pk=attempt.subject_id)
            attempt = subject.attempts.get(pk=attempt.pk)
            if not attempt.stripe_session_id:
                if not attempt.stripe_customer_id or attempt.status != "open":
                    return False
                from .stripe_service import _dict
                sessions = _stripe().checkout.Session.list(customer=attempt.stripe_customer_id, limit=100)
                matches = []
                for item in sessions.auto_paging_iter():
                    session = _dict(item)
                    meta = session.get("metadata") or {}
                    if (session.get("customer") == attempt.stripe_customer_id
                            and session.get("mode") == "subscription"
                            and meta.get("object_checkout_attempt") == attempt.reference
                            and meta.get("app") == subject.app
                            and str(meta.get("user_id")) == str(attempt.payer_id)
                            and meta.get("plan_key") == attempt.plan_key
                            and session.get("id")):
                        matches.append(session)
                        if len(matches) > 1:
                            return False
                if len(matches) != 1:
                    return False
                attempt.stripe_session_id = matches[0]["id"]
                if matches[0].get("status") == "open":
                    attempt.stripe_checkout_url = matches[0].get("url") or ""
                    expiry = matches[0].get("expires_at")
                    attempt.expires_at = datetime.fromtimestamp(expiry, tz=dt_timezone.utc) if expiry else None
                attempt.save(update_fields=("stripe_session_id", "stripe_checkout_url", "expires_at", "updated_at"))
    from .stripe_service import _dict
    session = _dict(_stripe().checkout.Session.retrieve(attempt.stripe_session_id))
    meta = session.get("metadata") or {}
    if session.get("status") == "complete" and session.get("payment_status") in ("paid", "no_payment_required"):
        from .object_lifecycle import handle_object_event
        return handle_object_event("checkout.session.completed", session, meta, {})
    if session.get("status") == "complete":
        if _terminal(attempt):
            return update_object_attempt("checkout.session.async_payment_failed", session, meta)
        return update_object_attempt("checkout.session.completed", session, meta)
    if session.get("status") == "expired":
        return update_object_attempt("checkout.session.expired", session, meta)
    return False
