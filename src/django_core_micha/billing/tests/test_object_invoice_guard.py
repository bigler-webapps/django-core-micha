from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import logging

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import Contract, ContractPayment, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription, Purchase
from django_core_micha.billing.object_access import object_access_state
from django_core_micha.billing.object_lifecycle import handle_object_event


@pytest.mark.django_db
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("reason", ["future", "other_plan"])
@pytest.mark.parametrize("delivery", ["webhook", "reconcile"])
def test_guarded_invoice_has_no_effect(monkeypatch, caplog, existing, reason, delivery):
    user = get_user_model().objects.create_user(username="payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="ref", plan_key="film", idempotency_key="key", stripe_price_id="price", stripe_customer_id="cus", status="completed" if existing else "open")
    now = timezone.now().replace(microsecond=0)
    end = now + timedelta(days=3)
    if existing:
        ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub", stripe_customer_id="cus", status="active", current_period_start=now-timedelta(days=20), current_period_end=end, cancel_at_period_end=True)
        Contract.objects.create(user=user, app="cinema", kind="object_subscription", plan_key="film", stripe_object_id="sub", status="cancellation_scheduled", cancel_effective_at=end)
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5)
    cfg = SimpleNamespace(app="cinema", plans=[plan], currency="chf", contract=None)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_access.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.stripe_service._invoice_plan_key", lambda invoice: invoice.get("test_plan", "film"))
    meta = {"app":"cinema", "user_id":str(user.pk), "plan_key":"film", "object_checkout_attempt":"ref"}
    remote = {"id":"sub", "customer":"cus", "metadata":meta, "status":"active", "cancel_at_period_end":existing}
    start = now + timedelta(days=1) if reason == "future" else now - timedelta(days=1)
    invoice = {"id":"guarded", "subscription":"sub", "status":"paid", "billing_reason":"subscription_update", "period_start":int(start.timestamp()), "period_end":int((end+timedelta(days=30)).timestamp()), "test_plan":"other" if reason == "other_plan" else "film"}
    stale = dict(invoice, id="stale", test_plan="film", period_start=int((now-timedelta(days=30)).timestamp()), period_end=int((now-timedelta(days=2)).timestamp()))
    provider = SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value=remote)), Invoice=SimpleNamespace(list=Mock(return_value=SimpleNamespace(data=[invoice, invoice, stale] if existing else [invoice, invoice]))))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: provider)
    with override_settings(DCM_BILLING_APP="cinema"), caplog.at_level(logging.ERROR):
        before = object_access_state(user=user, object_key="film", authorize=lambda u,k: True)
        for _ in range(2):
            if delivery == "webhook":
                handle_object_event("invoice.paid", invoice, meta, {})
            else:
                handle_object_event("customer.subscription.updated", remote, meta, {})
        after = object_access_state(user=user, object_key="film", authorize=lambda u,k: True)
    assert before == after
    attempt.refresh_from_db()
    assert attempt.status == ("completed" if existing else "open")
    assert ObjectSubscription.objects.count() == int(existing)
    assert not Purchase.objects.exists()
    assert not ContractPayment.objects.filter(stripe_invoice_id="guarded").exists()
    if existing:
        contract = Contract.objects.get(); assert contract.cancel_effective_at == end
    assert "Guarded paid object invoice=guarded subscription=sub" in caplog.text


@pytest.mark.django_db
def test_started_same_plan_renewal_extends_paid_through(monkeypatch):
    user = get_user_model().objects.create_user(username="renew")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="ref", plan_key="film", idempotency_key="key", stripe_price_id="price", stripe_customer_id="cus", status="completed")
    now = timezone.now().replace(microsecond=0)
    sub = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub", status="active", current_period_start=now-timedelta(days=30), current_period_end=now-timedelta(days=1))
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5)
    cfg = SimpleNamespace(app="cinema", plans=[plan], currency="chf", contract=None)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    meta = {"app":"cinema", "user_id":str(user.pk), "plan_key":"film", "object_checkout_attempt":"ref"}
    remote = {"id":"sub", "customer":"cus", "metadata":meta, "status":"active"}
    end = now + timedelta(days=30)
    invoice = {"id":"renewal", "subscription":"sub", "status":"paid", "billing_reason":"subscription_cycle", "period_start":int((now-timedelta(days=1)).timestamp()), "period_end":int(end.timestamp())}
    Contract.objects.create(user=user, app="cinema", kind="object_subscription", plan_key="film", stripe_object_id="sub")
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value=remote))))
    with override_settings(DCM_BILLING_APP="cinema"):
        handle_object_event("invoice.paid", invoice, meta, {})
    sub.refresh_from_db(); assert sub.current_period_end == end and sub.plan_key == "film"

@pytest.mark.django_db
def test_personal_allowance_renews_once_without_changing_object(monkeypatch):
    from django.db import transaction
    from django_core_micha.billing.models import TokenEntry
    from django_core_micha.billing.stripe_service import _upsert_subscription
    user = get_user_model().objects.create_user(username="independent-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    now = timezone.now().replace(microsecond=0)
    end = now + timedelta(days=30)
    sub = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="object-sub", status="active", current_period_end=end)
    plan = Plan(key="personal", kind="subscription", name="Personal", amount_cents=100, interval="month", token_allowance=25)
    monkeypatch.setattr("django_core_micha.billing.stripe_service._plan", lambda key: plan if key == "personal" else None)
    start = now + timedelta(days=1)
    remote = {"id":"personal-sub", "status":"active", "metadata":{"app":"cinema", "user_id":str(user.pk), "plan_key":"personal"}, "current_period_start":int(start.timestamp()), "current_period_end":int((start+timedelta(days=30)).timestamp())}
    with override_settings(DCM_BILLING_APP="cinema"), transaction.atomic():
        _upsert_subscription(remote)
        _upsert_subscription(remote)
    entries = TokenEntry.objects.filter(account__user=user, reference=f"stripe:personal-sub:{int(start.timestamp())}")
    assert entries.count() == 1 and entries.get().remaining == 25
    sub.refresh_from_db()
    assert (sub.plan_key, sub.current_period_end) == ("film", end)
