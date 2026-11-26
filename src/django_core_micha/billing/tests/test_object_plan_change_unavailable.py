import inspect
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from django_core_micha.billing import contracts, object_portal, views
from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import Contract, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription
from django_core_micha.billing.object_checkout import ObjectCheckoutConflict, start_object_checkout
from django_core_micha.billing.urls import urlpatterns


def test_billing_route_and_object_action_inventory():
    # Any new endpoint or object portal argument requires a review of plan-change behavior.
    assert {(str(p.pattern), p.callback.__name__) for p in urlpatterns} == {
        ("me/", "me"), ("team/seats/", "team_seats"), ("cost-report/", "cost_report"),
        ("checkout/", "checkout"), ("checkout/attempts/<str:reference>/", "checkout_attempt"),
        ("portal/", "portal"), ("withdrawal/", "withdrawal_list"),
        ("withdrawal/<int:purchase_id>/preview/", "withdrawal_preview"),
        ("withdrawal/<int:purchase_id>/", "withdrawal_execute"), ("webhook/", "webhook"),
        ("public/declarations/csrf/", "public_declarations_csrf"),
        ("public/declarations/", "public_declarations"),
        ("public/declarations/confirm/", "public_declarations_confirm"),
        ("contracts/", "contracts_list"), ("contracts/<str:reference>/cancel/", "contract_cancel"),
        ("contracts/<str:reference>/withdraw/", "contract_withdraw"),
        ("operator/overview/", "overview"), ("operator/accounts/", "accounts"),
        ("operator/accounts/<int:user_id>/free-access/", "free_access"),
        ("operator/declarations/", "declarations"),
    }
    assert set(inspect.signature(object_portal.create_object_portal).parameters) == {"user", "subscription_id", "return_url"}
    assert set(inspect.signature(contracts.apply_cancellation).parameters) == {"declaration", "operator_accepted"}
    assert set(inspect.signature(contracts.apply_withdrawal).parameters) == {"declaration", "operator_accepted"}


@pytest.mark.django_db
@pytest.mark.parametrize("change", [{"plan_key": "other"}, {"plan_key": "film", "interval": "year"}])
def test_object_plan_change_has_no_checkout_or_contract_handler(monkeypatch, settings, change):
    settings.DCM_BILLING_APP = "cinema"
    user = get_user_model().objects.create_user(username="payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    sub = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub", status="active")
    contract = Contract.objects.create(user=user, app="cinema", kind="object_subscription", plan_key="film", stripe_object_id="sub")
    plans = [Plan(key=key, kind="object_subscription", name=key, amount_cents=1000, interval="month", member_limit=5, tax_code="tax") for key in ("film", "other")]
    cfg = SimpleNamespace(app="cinema", plans=plans, contract={"enabled": True})
    monkeypatch.setattr(views, "load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config", lambda: cfg)
    provider = Mock()
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", provider)
    request = RequestFactory().post("/checkout/", data=json.dumps(change), content_type="application/json")
    request.user = user
    request._dont_enforce_csrf_checks = True
    assert views.checkout(request).status_code == 400
    with pytest.raises(ObjectCheckoutConflict):
        start_object_checkout(user=user, object_key="project", plan_key=change["plan_key"], idempotency_key="a" * 16, authorize=lambda u, k: True, success_url="https://app.test/ok", cancel_url="https://app.test/cancel")
    declaration = SimpleNamespace(reference="declaration", status="scheduled", received_at=timezone.now(), effective_at=None)
    create_declaration = Mock(return_value=declaration)
    monkeypatch.setattr("django_core_micha.billing.declarations.create_owner_declaration", create_declaration)
    request = RequestFactory().post("/contracts/cancel/", data=json.dumps(change), content_type="application/json")
    request.user = user
    request._dont_enforce_csrf_checks = True
    assert views.contract_cancel(request, contract.reference).status_code == 201
    assert create_declaration.call_args.args == (contract, "cancellation")
    assert "plan_key" not in create_declaration.call_args.kwargs
    assert "interval" not in create_declaration.call_args.kwargs
    request = RequestFactory().post("/contracts/withdraw/", data=json.dumps(change), content_type="application/json")
    request.user = user
    request._dont_enforce_csrf_checks = True
    assert views.contract_withdraw(request, contract.reference).status_code == 400
    sub.refresh_from_db(); contract.refresh_from_db()
    assert sub.plan_key == contract.plan_key == "film"
    provider.assert_not_called()


@pytest.mark.django_db
def test_object_portal_disables_subscription_changes_and_schedules(monkeypatch):
    user = get_user_model().objects.create_user(username="portal-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    sub = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub", stripe_customer_id="cus", status="active")
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="attempt", plan_key="film", idempotency_key="key", stripe_price_id="price", stripe_customer_id="cus", status="completed")
    create = Mock(return_value=SimpleNamespace(id="config"))
    provider = SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value={"id": "sub", "customer": "cus"})), billing_portal=SimpleNamespace(Configuration=SimpleNamespace(create=create), Session=SimpleNamespace(create=Mock(return_value=SimpleNamespace(url="https://stripe.test")))))
    monkeypatch.setattr(object_portal, "load_billing_config", lambda: SimpleNamespace(app="cinema"))
    monkeypatch.setattr(object_portal, "_stripe", lambda: provider)
    assert object_portal.create_object_portal(user=user, subscription_id=sub.pk, return_url="https://app.test") == "https://stripe.test"
    features = create.call_args.kwargs["features"]
    assert features["subscription_update"]["enabled"] is False
    assert all(not value.get("enabled", False) for key, value in features.items() if "schedule" in key)
