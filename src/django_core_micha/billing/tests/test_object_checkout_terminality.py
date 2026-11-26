from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import ObjectCheckoutAttempt
from django_core_micha.billing.object_checkout import (
    ObjectCheckoutConflict, reconcile_object_attempt, start_object_checkout, update_object_attempt,
)


@pytest.fixture
def checkout(monkeypatch, settings):
    settings.DCM_BILLING_APP = "cinema"
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000,
                interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], contract=None)
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._latest_price", lambda _: SimpleNamespace(stripe_price_id="price"))
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe", lambda: SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_" + kw["metadata"]["object_checkout_attempt"]))))
    sessions = {}
    keys = {}
    def create(**params):
        key = params["idempotency_key"]
        if key not in keys:
            sid = "cs_" + str(len(keys) + 1)
            keys[key] = sid
            sessions[sid] = {"id": sid, "status": "open", "payment_status": "unpaid",
                             "subscription": None, "customer": params["customer"], "metadata": params["metadata"], "after_expiration": None}
        return SimpleNamespace(id=keys[key], url="https://stripe.test/checkout", expires_at=None)
    api = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(create=Mock(side_effect=create), retrieve=Mock(side_effect=lambda sid: sessions[sid]))),
                          Subscription=SimpleNamespace(retrieve=Mock()), Invoice=SimpleNamespace(retrieve=Mock()),
                          PaymentIntent=SimpleNamespace(retrieve=Mock()))
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: api)
    buyer = get_user_model().objects.create_user(username="first")
    other = get_user_model().objects.create_user(username="second")
    args = dict(object_key="film-1", plan_key="film", idempotency_key="a" * 16,
                authorize=lambda u, k: True, success_url="https://app.test/ok", cancel_url="https://app.test/cancel")
    return SimpleNamespace(api=api, sessions=sessions, keys=keys, buyer=buyer, other=other, args=args, create=create)


@pytest.mark.django_db
def test_retry_after_lost_create_response_and_retention_limit(checkout):
    c = checkout
    def lost(**params):
        c.create(**params)
        raise ConnectionError("response lost")
    c.api.checkout.Session.create.side_effect = lost
    with pytest.raises(ConnectionError):
        start_object_checkout(user=c.buyer, **c.args)
    c.api.checkout.Session.create.side_effect = c.create
    recovered = start_object_checkout(user=c.buyer, **c.args)
    assert recovered.stripe_session_id == next(iter(c.sessions))
    with pytest.raises(ObjectCheckoutConflict):
        start_object_checkout(user=c.other, **c.args)
    attempt = ObjectCheckoutAttempt.objects.get()
    assert attempt.status == "open" and attempt.stripe_session_id == recovered.stripe_session_id
    assert len(c.sessions) == 1
    # A separate unresolved attempt must not be replayed after key retention.
    c.api.checkout.Session.create.side_effect = lost
    second_args = dict(c.args, object_key="film-2", idempotency_key="b" * 16)
    with pytest.raises(ConnectionError):
        start_object_checkout(user=c.buyer, **second_args)
    attempt = ObjectCheckoutAttempt.objects.get(subject__object_key="film-2")
    attempt.created_at = timezone.now() - timedelta(hours=24)
    attempt.save()
    with pytest.raises(ObjectCheckoutConflict, match="session_creation_unconfirmed"):
        start_object_checkout(user=c.buyer, **second_args)
    assert len(c.sessions) == 2


@pytest.mark.django_db
def test_expiry_requires_current_provider_state_and_reconciles(checkout):
    c = checkout
    attempt = start_object_checkout(user=c.buyer, **c.args)
    session = c.sessions[attempt.stripe_session_id]
    stale = dict(session, status="expired")
    assert not update_object_attempt("checkout.session.expired", stale, session["metadata"])
    with pytest.raises(ObjectCheckoutConflict):
        start_object_checkout(user=c.other, **c.args)
    c.api.checkout.Session.retrieve.side_effect = ConnectionError("provider unavailable")
    with pytest.raises(ConnectionError):
        update_object_attempt("checkout.session.expired", stale, session["metadata"])
    attempt.refresh_from_db()
    assert attempt.status == "open"
    c.api.checkout.Session.retrieve.side_effect = lambda sid: c.sessions[sid]
    session["status"] = "expired"
    assert reconcile_object_attempt(attempt.pk)
    replacement = start_object_checkout(user=c.other, **c.args)
    assert replacement.stripe_session_id == "cs_2"
    assert len(c.sessions) == 2


@pytest.mark.django_db
def test_delayed_failure_blocks_until_subscription_and_payment_are_terminal(checkout):
    c = checkout
    attempt = start_object_checkout(user=c.buyer, **c.args)
    session = c.sessions[attempt.stripe_session_id]
    session.update(status="complete", subscription="sub_1")
    c.api.Subscription.retrieve.return_value = {"id": "sub_1", "status": "incomplete", "latest_invoice": "in_1"}
    assert reconcile_object_attempt(attempt.pk)
    assert not update_object_attempt("checkout.session.async_payment_failed", session, session["metadata"])
    with pytest.raises(ObjectCheckoutConflict):
        start_object_checkout(user=c.other, **c.args)
    c.api.Subscription.retrieve.return_value["status"] = "incomplete_expired"
    c.api.Invoice.retrieve.return_value = {"id": "in_1", "status": "void", "payment_intent": "pi_1"}
    c.api.PaymentIntent.retrieve.return_value = {"id": "pi_1", "status": "processing"}
    assert not update_object_attempt("checkout.session.async_payment_failed", session, session["metadata"])
    c.api.PaymentIntent.retrieve.return_value["status"] = "canceled"
    assert update_object_attempt("checkout.session.async_payment_failed", session, session["metadata"])
    start_object_checkout(user=c.other, **c.args)
    assert len(c.sessions) == 2


@pytest.mark.django_db
def test_legacy_released_attempt_cannot_hide_late_payment(checkout):
    c = checkout
    attempt = start_object_checkout(user=c.buyer, **c.args)
    session = c.sessions[attempt.stripe_session_id]
    ObjectCheckoutAttempt.objects.filter(pk=attempt.pk).update(status="failed")
    session.update(status="complete", payment_status="paid", subscription="sub_paid")
    with pytest.raises(ObjectCheckoutConflict, match="previous_attempt_unconfirmed"):
        start_object_checkout(user=c.other, **c.args)
    assert len(c.sessions) == 1


@pytest.mark.django_db
def test_creation_error_does_not_release_a_possibly_created_session(checkout):
    import stripe
    c = checkout
    c.api.checkout.Session.create.side_effect = stripe.InvalidRequestError("invalid", "line_items", http_status=400)
    with pytest.raises(stripe.InvalidRequestError):
        start_object_checkout(user=c.buyer, **c.args)
    assert ObjectCheckoutAttempt.objects.get().status == "open"
    c.api.checkout.Session.create.side_effect = c.create
    with pytest.raises(ObjectCheckoutConflict):
        start_object_checkout(user=c.other, **c.args)
    replacement = start_object_checkout(user=c.buyer, **c.args)
    assert replacement.status == "open"
    assert len(c.sessions) == 1
