"""Isolated provider customers and payer-only portal sessions for object purchases."""

from .config import load_billing_config
from .models import BillingCustomer, ObjectCheckoutAttempt, ObjectSubscription
from .stripe_service import _dict, _stripe


def object_customer(attempt):
    """Called under the locked subject; Stripe idempotency survives a database retry."""
    if attempt.stripe_customer_id:
        return attempt.stripe_customer_id
    customer = _stripe().Customer.create(
        email=getattr(attempt.payer, "email", "") or None,
        metadata={"app": attempt.subject.app, "object_checkout_attempt": attempt.reference,
                  "user_id": str(attempt.payer_id)},
        idempotency_key=f"dcm-object-customer:{attempt.subject.app}:{attempt.reference}",
    )
    customer_id = customer.id
    if (not customer_id or BillingCustomer.objects.filter(stripe_customer_id=customer_id).exists()
            or ObjectCheckoutAttempt.objects.exclude(pk=attempt.pk).filter(stripe_customer_id=customer_id).exists()):
        raise ValueError("customer_not_isolated")
    attempt.stripe_customer_id = customer_id
    attempt.save(update_fields=("stripe_customer_id", "updated_at"))
    return customer_id


def _isolated_subscription(user, subscription_id):
    """Verify the payer and the locally isolated customer against Stripe."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        raise PermissionError("authentication_required")
    sub = ObjectSubscription.objects.filter(pk=subscription_id, payer=user,
        subject__app=load_billing_config().app).first()
    if not sub:
        raise LookupError("subscription_not_found")
    customer_id = sub.stripe_customer_id
    attempt = ObjectCheckoutAttempt.objects.filter(subject=sub.subject, payer=user,
        status="completed", stripe_customer_id=customer_id).first() if customer_id else None
    if (not attempt or BillingCustomer.objects.filter(stripe_customer_id=customer_id).exists()
            or ObjectSubscription.objects.filter(stripe_customer_id=customer_id).exclude(pk=sub.pk).exists()):
        raise ValueError("customer_not_isolated")
    remote = _dict(_stripe().Subscription.retrieve(sub.stripe_subscription_id))
    remote_customer = remote.get("customer")
    if isinstance(remote_customer, dict):
        remote_customer = remote_customer.get("id")
    if remote.get("id") != sub.stripe_subscription_id or remote_customer != customer_id:
        raise ValueError("customer_not_isolated")
    return sub, remote


def create_object_portal(*, user, subscription_id, return_url):
    """Open billing details for the payer using a trusted return URL."""
    sub, _ = _isolated_subscription(user, subscription_id)
    customer_id = sub.stripe_customer_id
    provider = _stripe()
    # Disable customer-wide subscription operations, including after a portal flow.
    config = provider.billing_portal.Configuration.create(features={
        "subscription_update": {"enabled": False},
        "subscription_cancel": {"enabled": False},
        "invoice_history": {"enabled": True},
        "payment_method_update": {"enabled": True},
        "customer_update": {"enabled": False},
    })
    return provider.billing_portal.Session.create(customer=customer_id, configuration=config.id,
                                                   return_url=return_url).url


