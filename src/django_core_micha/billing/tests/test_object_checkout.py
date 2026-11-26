from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import ObjectBillingSubject
from django_core_micha.billing.object_checkout import ObjectCheckoutConflict, start_object_checkout, update_object_attempt
from django_core_micha.billing.stripe_service import _create_plan_price, _verify_plan_price


@pytest.mark.django_db
def test_object_checkout_reservation_and_delayed_payment(monkeypatch):
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], contract=None)
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._latest_price", lambda key: SimpleNamespace(stripe_price_id="price"))
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe", lambda: SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_" + kw["metadata"]["object_checkout_attempt"]))))
    create = Mock(side_effect=[SimpleNamespace(id=f"cs_{n}", url="https://stripe.test", expires_at=1) for n in (1, 2)])
    retrieve = Mock()
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(create=create, retrieve=retrieve))))
    buyer = get_user_model().objects.create_user(username="buyer")
    other = get_user_model().objects.create_user(username="other")
    args = dict(object_key="film-1", plan_key="film", idempotency_key="a" * 16, authorize=lambda u, k: True, success_url="https://app.test/ok", cancel_url="https://app.test/cancel")
    with override_settings(DCM_BILLING_APP="cinema"):
        with pytest.raises(PermissionError):
            start_object_checkout(user=buyer, **{**args, "authorize": lambda u, k: False})
        attempt = start_object_checkout(user=buyer, **args)
        assert start_object_checkout(user=buyer, **args).pk == attempt.pk
        with pytest.raises(ObjectCheckoutConflict):
            start_object_checkout(user=other, **args)
        metadata = create.call_args.kwargs["metadata"]
        customer = create.call_args.kwargs["customer"]
        assert create.call_args.kwargs["mode"] == "subscription"
        session = {"id": "cs_1", "customer": customer, "payment_status": "unpaid", "metadata": metadata, "status": "open"}
        retrieve.return_value = session
        assert update_object_attempt("checkout.session.completed", session, metadata)
        assert not update_object_attempt("checkout.session.expired", session, metadata)
        with pytest.raises(ObjectCheckoutConflict):
            start_object_checkout(user=other, **args)
        session["status"] = "expired"
        assert update_object_attempt("checkout.session.async_payment_failed", session, metadata)
        start_object_checkout(user=other, **args)
    assert create.call_count == 2
    assert not ObjectBillingSubject.objects.get().subscriptions.exists()


def test_object_price_is_inclusive_and_recurring():
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="year", member_limit=5, tax_code="tax")
    stripe = SimpleNamespace(Price=SimpleNamespace(create=Mock()), Product=SimpleNamespace(retrieve=Mock(return_value={"active": True, "metadata": {"app": "cinema", "plan_key": "film"}, "tax_code": "tax"})))
    _create_plan_price(stripe, app="cinema", plan=plan, currency="chf", lookup_key="cinema:film:v1", product_id="prod", version=1)
    assert stripe.Price.create.call_args.kwargs["tax_behavior"] == "inclusive"
    assert stripe.Price.create.call_args.kwargs["recurring"] == {"interval": "year"}
    price = {"active": True, "lookup_key": "cinema:film:v1", "product": "prod", "currency": "chf", "unit_amount": 1000, "recurring": {"interval": "year"}, "tax_behavior": "inclusive", "metadata": {"app": "cinema", "plan_key": "film", "version": "1"}}
    assert _verify_plan_price(stripe, price, app="cinema", plan=plan, currency="chf", lookup_key="cinema:film:v1") == price
