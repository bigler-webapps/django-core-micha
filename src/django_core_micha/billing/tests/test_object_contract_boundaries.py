from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing.contracts import apply_cancellation, _stop_future_charges
from django_core_micha.billing.models import BillingCustomer, Contract, Declaration, ObjectBillingSubject, ObjectSubscription
from django_core_micha.billing.views import portal


@pytest.mark.django_db
def test_due_cancellation_skips_unknown_period_and_processes_later_declarations():
    from django_core_micha.billing.contracts import apply_due_cancellations
    from django_core_micha.billing.models import Subscription
    payer = get_user_model().objects.create_user(username="due-payer")
    now = timezone.now()
    declarations = []
    for sid, end in (("sub-null", None), ("sub-due", now - timedelta(days=1)), ("sub-canceled", None)):
        Subscription.objects.create(user=payer, plan_key="personal", stripe_subscription_id=sid, status="canceled" if sid == "sub-canceled" else "active", current_period_end=end)
        contract = Contract.objects.create(user=payer, app="cinema", kind="subscription", plan_key="personal", stripe_object_id=sid)
        declarations.append(Declaration.objects.create(app="cinema", user=payer, contract=contract, kind="cancellation", channel="account", status="scheduled", effective_at=now - timedelta(days=1)))
    with override_settings(DCM_BILLING_APP="cinema"):
        assert apply_due_cancellations(now) == 2
    for declaration in declarations:
        declaration.refresh_from_db()
    assert declarations[0].status == "scheduled"
    assert declarations[1].status == "effective"
    assert declarations[2].status == "effective"


@pytest.mark.django_db
def test_object_cancellation_uses_subscription(monkeypatch):
    payer = get_user_model().objects.create_user(username="contract-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    end = timezone.now() + timedelta(days=30)
    sub = ObjectSubscription.objects.create(subject=subject, payer=payer, plan_key="film", stripe_subscription_id="sub-object", status="active", current_period_end=end)
    contract = Contract.objects.create(user=payer, app="cinema", kind="object_subscription", plan_key="film", stripe_object_id="sub-object", initial_term_end=end)
    declaration = Declaration.objects.create(app="cinema", user=payer, contract=contract, kind="cancellation", channel="account", cancellation_type="ordinary")
    modify = Mock()
    stripe = SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value={"schedule":"sched"}), modify=Mock()), SubscriptionSchedule=SimpleNamespace(retrieve=Mock(return_value={"phases":[{"start_date":1,"end_date":2,"items":[{"price":"old"}]}]}), modify=modify))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    apply_cancellation(declaration)
    sub.refresh_from_db(); contract.refresh_from_db(); declaration.refresh_from_db()
    assert declaration.status == "scheduled" and declaration.effective_at == end
    assert sub.cancel_at_period_end and contract.cancel_effective_at == end
    modify.assert_not_called()
    stripe.Subscription.modify.assert_called_once_with("sub-object", cancel_at_period_end=True)


@pytest.mark.django_db
def test_object_withdrawal_stops_only_its_subscription_and_account_portal_is_closed(monkeypatch):
    payer = get_user_model().objects.create_user(username="object-payer-isolation")
    BillingCustomer.objects.create(user=payer, stripe_customer_id="customer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="one")
    other = ObjectBillingSubject.objects.create(app="cinema", object_key="two")
    sub = ObjectSubscription.objects.create(subject=subject, payer=payer, plan_key="film", stripe_subscription_id="sub-one", status="active")
    other_sub = ObjectSubscription.objects.create(subject=other, payer=payer, plan_key="film", stripe_subscription_id="sub-two", status="active")
    contract = Contract.objects.create(user=payer, app="cinema", kind="object_subscription", plan_key="film", stripe_object_id="sub-one")
    cancel = Mock()
    create = Mock()
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(cancel=cancel), billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create))))
    request = RequestFactory().post("/portal/"); request.user = payer
    with override_settings(DCM_BILLING_APP="cinema"):
        assert portal(request).status_code == 409
        _stop_future_charges(contract, timezone.now())
    sub.refresh_from_db(); other_sub.refresh_from_db()
    cancel.assert_called_once_with("sub-one")
    create.assert_not_called()
    assert sub.status == "canceled" and not sub.blocks_purchase
    assert other_sub.status == "active" and other_sub.blocks_purchase


@pytest.mark.django_db
@pytest.mark.parametrize("contextual", [False, True])
def test_legacy_checkout_rejects_object_plan_before_provider(monkeypatch, settings, contextual):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from django_core_micha.billing import views
    from django_core_micha.billing.config import Plan
    from django_core_micha.billing import stripe_service
    from django_core_micha.billing import config
    user = get_user_model().objects.create_user(username="buyer")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    monkeypatch.setattr(views, "load_billing_config", lambda: SimpleNamespace(plans=[plan]))
    monkeypatch.setattr(config, "load_checkout_contexts", lambda: ({"account": {}}, None))
    provider = Mock()
    monkeypatch.setattr(stripe_service, "_stripe", provider)
    data = {"plan_key": "film"}
    if contextual:
        data.update(context="account", idempotency_key="a" * 16)
    request = RequestFactory().post("/", data=json.dumps(data), content_type="application/json")
    request.user = user
    request._dont_enforce_csrf_checks = True
    response = views.checkout(request)
    assert response.status_code == 400
    assert json.loads(response.content)["detail"] == "unknown_plan"
    provider.assert_not_called()
