from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import Contract, ObjectBillingSubject, ObjectSubscription, Purchase
from django_core_micha.billing.object_access import object_access_state


@pytest.mark.django_db
@pytest.mark.parametrize("withdrawal", ["purchase", "contract"])
def test_withdrawn_later_subscription_does_not_hide_replacement(monkeypatch, withdrawal):
    user = get_user_model().objects.create_user(username="payer")
    now = timezone.now()
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    old = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="old", stripe_subscription_id="old", status="active", current_period_end=now + timedelta(days=60), blocks_purchase=False)
    if withdrawal == "purchase":
        Purchase.objects.create(user=user, kind="object_subscription", plan_key="old", stripe_object_id="old", status="withdrawn", amount_paid_cents=1000, currency="chf", purchased_at=now)
    else:
        Contract.objects.create(user=user, app="cinema", kind="object_subscription", plan_key="old", stripe_object_id="old", status="withdrawn")
    plan = Plan(key="replacement", kind="object_subscription", name="Replacement", amount_cents=1000, interval="month", member_limit=7, tax_code="tax")
    monkeypatch.setattr("django_core_micha.billing.object_access.load_billing_config", lambda: SimpleNamespace(app="cinema", plans=[plan]))
    state = lambda: object_access_state(user=user, object_key="project", authorize=lambda u, k: True)
    assert state()["billing_status"] == "withdrawn"
    assert state()["source"] == "none"
    replacement = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="replacement", stripe_subscription_id="new", status="active", current_period_end=now + timedelta(days=30))
    result = state()
    assert result["source"] == "subscription"
    assert result["billing_status"] == "active"
    assert result["plan_key"] == "replacement" and result["plan_name"] == "Replacement"
    assert result["member_limit"] == result["paid_member_limit"] == 7
    assert result["paid_through"] == replacement.current_period_end
