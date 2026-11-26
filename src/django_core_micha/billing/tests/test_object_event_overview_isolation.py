import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from django_core_micha.billing import operator
from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import (
    ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription, Purchase,
    Subscription, TokenAccount, TokenEntry,
)
from django_core_micha.billing.stripe_service import process_stripe_event


@pytest.mark.django_db
def test_paid_object_invoice_routes_without_personal_or_token_mutation(monkeypatch, settings):
    settings.DCM_BILLING_APP = "cinema"
    payer = get_user_model().objects.create_user(username="routing-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=payer, reference="ref", plan_key="film", idempotency_key="key", stripe_price_id="price", stripe_customer_id="cus")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], currency="chf", contract=None)
    for module in ("stripe_service", "contracts"):
        monkeypatch.setattr(f"django_core_micha.billing.{module}.load_billing_config", lambda: cfg)
    meta = {"app": "cinema", "user_id": str(payer.pk), "plan_key": "film", "object_checkout_attempt": "ref"}
    now = int(timezone.now().timestamp())
    remote = {"id": "sub_object", "customer": "cus", "metadata": meta, "status": "active"}
    invoice = {"id": "inv_object", "subscription": "sub_object", "status": "paid", "period_start": now, "period_end": now + 3600, "amount_paid": 1000, "currency": "chf", "billing_reason": "subscription_create"}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value=remote))))
    personal = Subscription.objects.create(user=payer, plan_key="personal", stripe_subscription_id="sub_personal", status="active", current_period_end=timezone.now() + timedelta(days=1))
    account = TokenAccount.objects.create(user=payer)
    TokenEntry.objects.create(account=account, kind="grant", amount=12, remaining=12, reference="existing")
    assert process_stripe_event({"type": "invoice.paid", "data": {"object": invoice}}) == "processed"
    assert ObjectSubscription.objects.get().stripe_subscription_id == "sub_object"
    personal.refresh_from_db()
    assert personal.plan_key == "personal" and Subscription.objects.count() == 1
    assert list(TokenEntry.objects.values_list("amount", "remaining", "reference")) == [(12, 12, "existing")]
    assert Purchase.objects.get().kind == "object_subscription"


@pytest.mark.django_db
def test_overview_object_counts_are_app_scoped_and_include_withdrawn_history(settings):
    settings.DCM_BILLING_APP = "cinema"
    settings.SECURITY_DEFAULT_LEVEL = "basic"
    settings.SECURITY_LEVELS = ("basic", "strong")
    admin = get_user_model().objects.create_user(username="overview-op", is_staff=True, is_superuser=True)
    now = timezone.now()
    for app, key, status in (("cinema", "active", "active"), ("cinema", "withdrawn", "withdrawn"), ("other", "foreign", "active")):
        subject = ObjectBillingSubject.objects.create(app=app, object_key=key)
        ObjectSubscription.objects.create(subject=subject, payer=admin, plan_key="film", stripe_subscription_id=key, status="active", current_period_end=now + timedelta(days=1), ended_at=now if key == "withdrawn" else None)
        Purchase.objects.create(user=admin, kind="object_subscription", plan_key="film", stripe_object_id=key, status=status, amount_paid_cents=100, currency="chf", purchased_at=now)
    req = RequestFactory().get("/operator/overview/")
    req.user = admin
    req.session = {"auth_level": "strong"}
    response = operator.overview(req)
    assert response.status_code == 200
    assert json.loads(response.content)["object_subscriptions"] == {"active": 1, "new": 2, "ended": 1}
