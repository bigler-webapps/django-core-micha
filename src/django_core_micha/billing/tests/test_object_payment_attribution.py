from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import ContractPayment, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription
from django_core_micha.billing.object_lifecycle import handle_object_event


@pytest.mark.django_db
def test_first_seen_renewal_recovers_initial_and_late_invoices(monkeypatch):
    payer = get_user_model().objects.create_user(username="attribution-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=payer, reference="attempt", plan_key="film", idempotency_key="key", stripe_price_id="price", stripe_customer_id="cus")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], currency="chf", contract=None)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.contracts.load_billing_config", lambda: cfg)
    meta = {"app": "cinema", "user_id": str(payer.pk), "plan_key": "film", "object_checkout_attempt": "attempt"}
    now = int(timezone.now().timestamp())
    def invoice(identifier, start, amount, reason):
        return {"id": identifier, "subscription": "sub", "status": "paid", "period_start": start, "period_end": start + 100, "created": start, "amount_paid": amount, "currency": "chf", "payment_intent": "pi_" + identifier, "billing_reason": reason}
    initial = invoice("initial", now - 300, 1000, "subscription_create")
    middle = invoice("middle", now - 200, 0, "subscription_cycle")
    latest = invoice("latest", now - 100, 1200, "subscription_cycle")
    remote = {"id": "sub", "customer": "cus", "metadata": meta, "status": "active"}
    provider = SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value=remote)), Invoice=SimpleNamespace(list=Mock(return_value=SimpleNamespace(data=[initial]))))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: provider)
    with override_settings(DCM_BILLING_APP="cinema"):
        handle_object_event("invoice.paid", latest, meta, {})
        handle_object_event("invoice.paid", middle, meta, {})
        handle_object_event("invoice.paid", middle, meta, {})
    payments = {p.stripe_invoice_id: (p.amount_paid_cents, p.billing_reason) for p in ContractPayment.objects.all()}
    assert payments == {"initial": (1000, "initial"), "middle": (0, "renewal"), "latest": (1200, "renewal")}
    assert int(ObjectSubscription.objects.get().current_period_start.timestamp()) == now - 100
