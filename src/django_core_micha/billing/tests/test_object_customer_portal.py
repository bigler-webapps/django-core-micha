from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from django_core_micha.billing.models import BillingCustomer, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription
from django_core_micha.billing.object_lifecycle import _valid_remote
from django_core_micha.billing.object_portal import create_object_portal, object_customer
from django_core_micha.billing.views import portal


@pytest.mark.django_db
def test_isolated_customers_portals_and_lifecycle(monkeypatch):
    payer = get_user_model().objects.create_user(username="payer")
    other = get_user_model().objects.create_user(username="other")
    BillingCustomer.objects.create(user=payer, stripe_customer_id="cus_personal")
    cfg = SimpleNamespace(app="cinema")
    monkeypatch.setattr("django_core_micha.billing.object_portal.load_billing_config", lambda: cfg)
    customer_create = Mock(side_effect=[SimpleNamespace(id="cus_one"), SimpleNamespace(id="cus_two")])
    config_create = Mock(return_value=SimpleNamespace(id="bpc_safe"))
    session_create = Mock(return_value=SimpleNamespace(url="https://stripe.test/portal"))
    remote = {"sub_one": {"id": "sub_one", "customer": "cus_one"}, "sub_two": {"id": "sub_two", "customer": "cus_two"}}
    provider = SimpleNamespace(Customer=SimpleNamespace(create=customer_create), Subscription=SimpleNamespace(retrieve=lambda sid: remote[sid]), billing_portal=SimpleNamespace(Configuration=SimpleNamespace(create=config_create), Session=SimpleNamespace(create=session_create)))
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe", lambda: provider)
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: provider)
    subscriptions = []
    for n, word in enumerate(("one", "two")):
        subject = ObjectBillingSubject.objects.create(app="cinema", object_key=word)
        attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=payer, reference=word, plan_key="film", idempotency_key=word, stripe_price_id="price", status="completed")
        assert object_customer(attempt) == f"cus_{word}"
        assert object_customer(attempt) == f"cus_{word}"
        sub = ObjectSubscription.objects.create(subject=subject, payer=payer, stripe_subscription_id=f"sub_{word}", stripe_customer_id=attempt.stripe_customer_id, plan_key="film", status="active")
        subscriptions.append(sub)
        meta = {"app": "cinema", "user_id": str(payer.pk), "plan_key": "film"}
        assert _valid_remote(attempt, {"id": sub.stripe_subscription_id, "customer": attempt.stripe_customer_id, "metadata": meta})
        assert not _valid_remote(attempt, {"id": sub.stripe_subscription_id, "customer": "cus_personal", "metadata": meta})
    assert customer_create.call_count == 2
    with pytest.raises(LookupError):
        create_object_portal(user=other, subscription_id=subscriptions[0].pk, return_url="https://app.test")
    assert create_object_portal(user=payer, subscription_id=subscriptions[0].pk, return_url="https://app.test") == "https://stripe.test/portal"
    assert session_create.call_args.kwargs["customer"] == "cus_one"
    assert session_create.call_args.kwargs["configuration"] == "bpc_safe"
    features = config_create.call_args.kwargs["features"]
    assert features["subscription_update"]["enabled"] is False
    assert features["subscription_cancel"]["enabled"] is False
    request = RequestFactory().post("/portal/"); request.user = payer
    assert portal(request).status_code == 200
    assert session_create.call_args.kwargs["customer"] == "cus_personal"
    remote["sub_one"]["customer"] = "cus_two"
    with pytest.raises(ValueError):
        create_object_portal(user=payer, subscription_id=subscriptions[0].pk, return_url="https://app.test")


@pytest.mark.django_db
def test_legacy_shared_customer_portals_fail_closed(monkeypatch, settings):
    settings.DCM_BILLING_APP = "cinema"
    payer = get_user_model().objects.create_user(username="legacy")
    BillingCustomer.objects.create(user=payer, stripe_customer_id="cus_old")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="legacy")
    sub = ObjectSubscription.objects.create(subject=subject, payer=payer, stripe_subscription_id="sub_old", plan_key="film", status="active")
    monkeypatch.setattr("django_core_micha.billing.object_portal.load_billing_config", lambda: SimpleNamespace(app="cinema"))
    with pytest.raises(ValueError):
        create_object_portal(user=payer, subscription_id=sub.pk, return_url="https://app.test")
    request = RequestFactory().post("/portal/"); request.user = payer
    assert portal(request).status_code == 409
    subject.app = "other"
    subject.save(update_fields=("app",))
    sub.stripe_customer_id = "cus_old"
    sub.save(update_fields=("stripe_customer_id",))
    assert portal(request).status_code == 409


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["subscription", "attempt_without_session", "attempt_with_session"])
def test_account_portal_rejects_unknown_cross_app_customer(monkeypatch, settings, kind):
    settings.DCM_BILLING_APP = "cinema"
    payer = get_user_model().objects.create_user(username="cross-app-payer")
    BillingCustomer.objects.create(user=payer, stripe_customer_id="cus_personal")
    subject = ObjectBillingSubject.objects.create(app="other-app", object_key="film")
    if kind == "subscription":
        ObjectSubscription.objects.create(subject=subject, payer=payer, stripe_subscription_id="sub_legacy", plan_key="film", status="active")
    else:
        ObjectCheckoutAttempt.objects.create(subject=subject, payer=payer, reference="legacy", plan_key="film", idempotency_key="legacy", stripe_price_id="price", stripe_session_id="cs_old" if kind == "attempt_with_session" else "")
    create = Mock(return_value=SimpleNamespace(url="https://stripe.test"))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create))))
    request = RequestFactory().post("/portal/"); request.user = payer
    assert portal(request).status_code == 409
    create.assert_not_called()


@pytest.mark.django_db
def test_account_portal_allows_isolated_cross_app_customer_and_other_payer(monkeypatch, settings):
    settings.DCM_BILLING_APP = "cinema"
    payer = get_user_model().objects.create_user(username="isolated-cross-app")
    other = get_user_model().objects.create_user(username="different-payer")
    BillingCustomer.objects.create(user=payer, stripe_customer_id="cus_personal")
    subject = ObjectBillingSubject.objects.create(app="other-app", object_key="film")
    ObjectSubscription.objects.create(subject=subject, payer=payer, stripe_subscription_id="sub_isolated", stripe_customer_id="cus_isolated", plan_key="film", status="active")
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=payer, reference="isolated", plan_key="film", idempotency_key="isolated", stripe_price_id="price", stripe_customer_id="cus_isolated")
    ObjectSubscription.objects.create(subject=subject, payer=other, stripe_subscription_id="sub_other", plan_key="film", status="canceled", blocks_purchase=False)
    create = Mock(return_value=SimpleNamespace(url="https://stripe.test"))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create))))
    request = RequestFactory().post("/portal/"); request.user = payer
    assert portal(request).status_code == 200
    assert create.call_args.kwargs["customer"] == "cus_personal"
