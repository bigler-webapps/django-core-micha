from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from django_core_micha.billing.config import Plan
from django_core_micha.billing.object_checkout import start_object_checkout
from django_core_micha.billing.models import BillingCustomer, ObjectCheckoutAttempt
from django_core_micha.billing.views import portal


@pytest.mark.django_db(transaction=True)
def test_checkout_uses_one_customer_per_attempt(monkeypatch):
    user = get_user_model().objects.create_user(username="buyer")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], contract=None)
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._latest_price", lambda key: SimpleNamespace(stripe_price_id="price"))
    customers = Mock(side_effect=[SimpleNamespace(id="cus_1"), SimpleNamespace(id="cus_2")])
    sessions = Mock(side_effect=[SimpleNamespace(id="cs_1", url="https://stripe.test/1", expires_at=1), SimpleNamespace(id="cs_2", url="https://stripe.test/2", expires_at=1)])
    provider = SimpleNamespace(Customer=SimpleNamespace(create=customers), checkout=SimpleNamespace(Session=SimpleNamespace(create=sessions)))
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe", lambda: provider)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: provider)
    common = dict(user=user, plan_key="film", idempotency_key="a" * 16, authorize=lambda u, k: True, success_url="https://app.test/ok", cancel_url="https://app.test/no")
    first = start_object_checkout(object_key="one", **common)
    assert start_object_checkout(object_key="one", **common).pk == first.pk
    second = start_object_checkout(object_key="two", **common)
    assert [c.kwargs["customer"] for c in sessions.call_args_list] == ["cus_1", "cus_2"]
    assert first.stripe_customer_id == "cus_1" and second.stripe_customer_id == "cus_2"
    assert customers.call_count == 2


@pytest.mark.django_db(transaction=True)
def test_failed_customer_creation_does_not_block_personal_portal(monkeypatch):
    user = get_user_model().objects.create_user(username="failed-object-buyer")
    BillingCustomer.objects.create(user=user, stripe_customer_id="cus_personal")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000,
                interval="month", member_limit=5, tax_code="tax")
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config",
                        lambda: SimpleNamespace(app="cinema", plans=[plan], contract=None))
    monkeypatch.setattr("django_core_micha.billing.object_checkout._latest_price",
                        lambda key: SimpleNamespace(stripe_price_id="price"))
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe",
                        lambda: SimpleNamespace(Customer=SimpleNamespace(create=Mock(side_effect=RuntimeError))))
    with pytest.raises(RuntimeError):
        start_object_checkout(user=user, object_key="film", plan_key="film", idempotency_key="a" * 16,
                              authorize=lambda u, k: True, success_url="https://app.test/ok",
                              cancel_url="https://app.test/no")
    attempt = ObjectCheckoutAttempt.objects.get(payer=user)
    assert attempt.isolated_customer_flow and not attempt.stripe_customer_id
    create = Mock(return_value=SimpleNamespace(url="https://stripe.test"))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",
                        lambda: SimpleNamespace(billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create))))
    request = RequestFactory().post("/portal/")
    request.user = user
    assert portal(request).status_code == 200
    assert create.call_args.kwargs["customer"] == "cus_personal"
