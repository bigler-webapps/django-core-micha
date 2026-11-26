from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import ObjectBillingSubject, ObjectSubscription
from django_core_micha.billing.object_access import object_access_state, set_object_grant


@pytest.mark.django_db
def test_paid_limit_with_grant_is_authorized(monkeypatch):
    payer = get_user_model().objects.create_user(username="grant-payer")
    other = get_user_model().objects.create_user(username="grant-other")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=100, interval="month", member_limit=5)
    monkeypatch.setattr("django_core_micha.billing.object_access.load_billing_config", lambda: SimpleNamespace(app="cinema", plans=[plan]))
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    ObjectSubscription.objects.create(subject=subject, payer=payer, plan_key="film", stripe_subscription_id="sub", status="active", current_period_end=timezone.now() + timedelta(days=1))
    set_object_grant(operator=payer, object_key="film", reason="support", grant=True, authorize=lambda u, k: True)
    state = object_access_state(user=payer, object_key="film", authorize=lambda u, k: True)
    assert (state["member_limit"], state["paid_member_limit"]) == (None, 5)
    with pytest.raises(PermissionError):
        object_access_state(user=other, object_key="film", authorize=lambda u, k: False)
