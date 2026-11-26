from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription, Purchase
from django_core_micha.billing.object_access import object_access_state, set_object_grant
from django_core_micha.billing.object_lifecycle import handle_object_event


@pytest.mark.django_db
def test_paid_invoice_and_reordered_subscription(monkeypatch):
    user = get_user_model().objects.create_user(username="object-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="ref", plan_key="film", idempotency_key="key", stripe_price_id="price", stripe_session_id="session", stripe_customer_id="cus")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], currency="chf", contract=None)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_access.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.contracts.load_billing_config", lambda: cfg)
    meta = {"app": "cinema", "user_id": str(user.pk), "plan_key": "film", "object_checkout_attempt": "ref"}
    now = int(timezone.now().timestamp())
    remote = {"id": "sub", "customer": "cus", "metadata": meta, "status": "active", "cancel_at_period_end": False}
    invoice = {"id": "inv", "subscription": "sub", "status": "paid", "period_start": now, "period_end": now + 3600, "amount_paid": 1000, "currency": "chf", "billing_reason": "subscription_create"}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(
        Subscription=SimpleNamespace(retrieve=Mock(return_value=remote)),
        Invoice=SimpleNamespace(list=Mock(return_value=SimpleNamespace(data=[]))),
    ))
    with override_settings(DCM_BILLING_APP="cinema"):
        assert handle_object_event("customer.subscription.updated", remote, meta, {})
        assert not ObjectSubscription.objects.exists()
        assert handle_object_event("invoice.paid", invoice, meta, {})
        assert handle_object_event("invoice.paid", invoice, meta, {})
        assert ObjectSubscription.objects.count() == 1
        assert Purchase.objects.get().tokens_granted == 0
        assert object_access_state(user=user, object_key="project", authorize=lambda u, k: True)["member_limit"] == 5
        Purchase.objects.filter(stripe_object_id="sub").update(status="withdrawn")
        handle_object_event("invoice.paid", invoice, meta, {})
        assert object_access_state(user=user, object_key="project", authorize=lambda u, k: True)["source"] == "none"
        attempt.refresh_from_db()
        assert attempt.status == "completed"


@pytest.mark.django_db
def test_object_grant_audits_and_preserves_paid_details(monkeypatch):
    user = get_user_model().objects.create_user(username="operator")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    monkeypatch.setattr("django_core_micha.billing.object_access.load_billing_config", lambda: SimpleNamespace(app="cinema", plans=[plan]))
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub", status="active", current_period_end=timezone.now() + timedelta(days=1))
    args = dict(operator=user, object_key="project", authorize=lambda u, k: True)
    with pytest.raises(ValueError):
        set_object_grant(**args, reason=" ", grant=True)
    grant = set_object_grant(**args, reason="support", grant=True)
    state = object_access_state(user=user, object_key="project", authorize=lambda u, k: True)
    assert state["source"] == "operator_grant" and state["member_limit"] is None and state["plan_key"] == "film"
    set_object_grant(**args, reason="ended", grant=False)
    grant.refresh_from_db()
    assert grant.revoked_by == user and grant.revocation_reason == "ended"
    assert object_access_state(user=user, object_key="project", authorize=lambda u, k: True)["source"] == "subscription"
