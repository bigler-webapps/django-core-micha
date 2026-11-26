from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import BillingCustomer, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription
from django_core_micha.billing.object_access import object_access_state
from django_core_micha.billing.object_checkout import start_object_checkout, update_object_attempt
from django_core_micha.billing.views import portal


@pytest.mark.django_db(transaction=True)
def test_lost_session_response_keeps_customer_and_retry_reuses_key(monkeypatch):
    user = get_user_model().objects.create_user(username="recover")
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000, interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], contract=None)
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._latest_price", lambda _: SimpleNamespace(stripe_price_id="price"))
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError()
        return SimpleNamespace(id="cs_one", url="https://stripe.test", expires_at=None)
    provider = SimpleNamespace(Customer=SimpleNamespace(create=Mock(return_value=SimpleNamespace(id="cus_one"))),
                               checkout=SimpleNamespace(Session=SimpleNamespace(create=create)))
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe", lambda: provider)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: provider)
    args = dict(user=user, object_key="film", plan_key="film", idempotency_key="a" * 16,
                authorize=lambda u, k: True, success_url="https://app.test/ok", cancel_url="https://app.test/no")
    with pytest.raises(TimeoutError):
        start_object_checkout(**args)
    attempt = ObjectCheckoutAttempt.objects.get()
    assert attempt.stripe_customer_id == "cus_one" and not attempt.stripe_session_id
    result = start_object_checkout(**args)
    assert result.stripe_session_id == "cs_one" and calls[0] == calls[1]
    assert provider.Customer.create.call_count == 1


@pytest.mark.django_db(transaction=True)
def test_lost_session_response_allows_verified_webhook(monkeypatch):
    user = get_user_model().objects.create_user(username="webhook-recover")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="ref", plan_key="film",
        idempotency_key="key", stripe_price_id="price", stripe_customer_id="cus")
    meta = {"app": "cinema", "user_id": str(user.pk), "plan_key": "film", "object_checkout_attempt": "ref"}
    session = {"id": "cs", "customer": "cus", "payment_status": "paid"}
    assert not update_object_attempt("checkout.session.completed", dict(session, customer="other"), meta)
    assert update_object_attempt("checkout.session.completed", session, meta)
    attempt.refresh_from_db()
    assert attempt.status == "paid" and attempt.stripe_session_id == "cs"


@pytest.mark.django_db
def test_access_denies_anonymous_and_unauthorized():
    user = get_user_model().objects.create_user(username="denied")
    for candidate, authorize in ((AnonymousUser(), lambda u, k: True), (user, lambda u, k: False)):
        with pytest.raises(PermissionError):
            object_access_state(user=candidate, object_key="film", authorize=authorize)


@pytest.mark.django_db
def test_personal_portal_rejects_other_apps_unknown_customer(monkeypatch):
    user = get_user_model().objects.create_user(username="portal-other-app")
    BillingCustomer.objects.create(user=user, stripe_customer_id="cus_personal")
    subject = ObjectBillingSubject.objects.create(app="other", object_key="film")
    ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub")
    create = Mock(return_value=SimpleNamespace(url="https://stripe.test"))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create))))
    request = RequestFactory().post("/")
    request.user = user
    with override_settings(DCM_BILLING_APP="cinema"):
        assert portal(request).status_code == 409
    create.assert_not_called()
