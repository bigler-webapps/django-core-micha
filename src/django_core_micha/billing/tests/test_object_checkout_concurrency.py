from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import RequestFactory
from django.utils import timezone

from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import (
    CheckoutAttempt, ContractPayment, ObjectBillingSubject, ObjectCheckoutAttempt,
    ObjectSubscription, Purchase, TokenAccount, TokenEntry,
)
from django_core_micha.billing.object_checkout import (
    ObjectCheckoutConflict, start_object_checkout, update_object_attempt,
)
from django_core_micha.billing.object_lifecycle import handle_object_event
from django_core_micha.billing.services import token_balance

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def provider(monkeypatch, settings):
    if connection.vendor != "postgresql" or not connection.features.has_select_for_update:
        pytest.skip("requires PostgreSQL with SELECT FOR UPDATE row locks")
    settings.DCM_BILLING_APP = "cinema"
    plan = Plan(key="film", kind="object_subscription", name="Film", amount_cents=1000,
                interval="month", member_limit=5, tax_code="tax")
    cfg = SimpleNamespace(app="cinema", plans=[plan], contract=None)
    monkeypatch.setattr("django_core_micha.billing.object_checkout.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.stripe_service.load_billing_config", lambda: cfg)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._latest_price", lambda _: SimpleNamespace(stripe_price_id="price"))
    state = SimpleNamespace(sessions={}, calls=[], lock=Lock(), entered=Event(), release=Event(), block=False)

    def create(**params):
        with state.lock:
            state.calls.append(params)
            sid = "cs_" + str(len(state.calls))
            state.sessions[sid] = {"id": sid, "status": "open", "payment_status": "unpaid",
                                   "subscription": None, "customer": params["customer"],
                                   "metadata": deepcopy(params["metadata"]), "after_expiration": None}
        if state.block:
            state.entered.set()
            if not state.release.wait(10):
                raise TimeoutError("provider stub was not released")
        return SimpleNamespace(id=sid, url="https://stripe.test/" + sid, expires_at=None)

    api = SimpleNamespace(
        Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_" + kw["metadata"]["object_checkout_attempt"])),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=create, retrieve=lambda sid: deepcopy(state.sessions[sid]))),
    )
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: api)
    monkeypatch.setattr("django_core_micha.billing.object_portal._stripe", lambda: api)
    state.cfg = cfg
    state.buyers = [get_user_model().objects.create_user(username=name) for name in ("buyer1", "buyer2")]
    return state


def start(user, object_key="project", key="a" * 16):
    return start_object_checkout(user=user, object_key=object_key, plan_key="film", idempotency_key=key,
                                 authorize=lambda u, k: True, success_url="https://app.test/ok",
                                 cancel_url="https://app.test/cancel")


def worker(fn, *args):
    close_old_connections()
    try:
        return fn(*args)
    finally:
        close_old_connections()


def test_two_buyers_and_same_buyer_retry_share_one_reservation(provider):
    p = provider
    p.block = True
    # Pre-existing subject makes the subject row the contention point.
    ObjectBillingSubject.objects.create(app="cinema", object_key="project")
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(worker, start, p.buyers[0])
        assert p.entered.wait(10)
        retry = pool.submit(worker, start, p.buyers[0])
        other = pool.submit(worker, start, p.buyers[1], "project", "b" * 16)
        try:
            assert not retry.done() or not other.done()
        finally:
            p.release.set()
        attempt = first.result(timeout=15)
        assert retry.result(timeout=15).pk == attempt.pk
        with pytest.raises(ObjectCheckoutConflict):
            other.result(timeout=15)
    assert len(p.calls) == 1
    assert ObjectCheckoutAttempt.objects.count() == 1
    assert not attempt.subject.subscriptions.exists()


def test_separate_objects_can_reach_provider_simultaneously_and_apps_are_isolated(provider):
    p = provider
    p.block = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, start, p.buyers[0], "one")
        second = pool.submit(worker, start, p.buyers[1], "two")
        try:
            # Both provider requests must start before either is allowed to finish.
            for _ in range(100):
                with p.lock:
                    if len(p.calls) == 2:
                        break
                Event().wait(.05)
            assert len(p.calls) == 2
        finally:
            p.release.set()
        assert first.result(timeout=15).subject_id != second.result(timeout=15).subject_id
    p.block = False
    p.cfg.app = "other-app"
    third = start(p.buyers[0], "one")
    assert third.subject.app == "other-app"
    assert ObjectBillingSubject.objects.filter(object_key="one").count() == 2
    assert len(p.calls) == 3


def test_simultaneous_reordered_events_use_current_provider_snapshot_and_allow_retry(provider):
    p = provider
    attempt = start(p.buyers[0])
    session = p.sessions[attempt.stripe_session_id]
    stale_expired = dict(deepcopy(session), status="expired")
    completed = dict(deepcopy(session), status="complete")
    barrier = Barrier(2)

    def apply(kind, snapshot):
        barrier.wait(timeout=10)
        return update_object_attempt(kind, snapshot, snapshot["metadata"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        expired = pool.submit(worker, apply, "checkout.session.expired", stale_expired)
        processing = pool.submit(worker, apply, "checkout.session.completed", completed)
        assert not expired.result(timeout=15)
        assert processing.result(timeout=15)
    attempt.refresh_from_db()
    assert attempt.status == "processing"
    with pytest.raises(ObjectCheckoutConflict):
        start(p.buyers[1], key="b" * 16)
    # Current provider state changes after the earlier stale event.
    session["status"] = "expired"
    assert update_object_attempt("checkout.session.expired", stale_expired, stale_expired["metadata"])
    replacement = start(p.buyers[1], key="b" * 16)
    assert replacement.pk != attempt.pk
    assert not update_object_attempt("checkout.session.expired", stale_expired, stale_expired["metadata"])
    assert replacement.status == "open"
    assert len(p.calls) == 2


def test_concurrent_object_and_token_checkout_are_independent(provider, monkeypatch, settings):
    from django_core_micha.billing import views

    p = provider
    p.block = True
    token_plan = Plan(key="tokens", kind="token_package", name="Tokens", amount_cents=500,
                      tokens=100, tax_code="tax")
    p.cfg.plans.append(token_plan)
    settings.DCM_BILLING_CHECKOUT_CONTEXTS = {
        "account": {"success_url": "https://app.test/ok", "cancel_url": "https://app.test/cancel"},
    }
    monkeypatch.setattr(views, "load_billing_config", lambda: p.cfg)
    monkeypatch.setattr(views, "get_customer", lambda user: SimpleNamespace(stripe_customer_id="cus_token"))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._latest_price", lambda _: SimpleNamespace(stripe_price_id="price_tokens"))
    token_calls = []
    def create_token(**params):
        token_calls.append(params)
        return SimpleNamespace(id="cs_token", url="https://stripe.test/token", expires_at=None)
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(
        checkout=SimpleNamespace(Session=SimpleNamespace(create=create_token))))

    def token_checkout():
        request = RequestFactory().post("/checkout/", data=json.dumps({
            "plan_key": "tokens", "context": "account", "idempotency_key": "token-key-123456",
        }), content_type="application/json")
        request.user = p.buyers[0]
        request._dont_enforce_csrf_checks = True
        return views.checkout(request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        object_future = pool.submit(worker, start, p.buyers[0])
        try:
            assert p.entered.wait(10)
            response = pool.submit(worker, token_checkout).result(timeout=10)
            assert response.status_code == 200
        finally:
            p.release.set()
        attempt = object_future.result(timeout=15)
    assert len(p.calls) == len(token_calls) == 1
    assert token_calls[0]["mode"] == "payment"
    assert CheckoutAttempt.objects.get().stripe_session_id == "cs_token"
    assert attempt.status == "open" and not ObjectSubscription.objects.exists()
    assert token_balance(p.buyers[0]) == 0


def test_concurrent_paid_events_do_not_duplicate_access_or_affect_tokens(provider, monkeypatch):
    p = provider
    p.cfg.currency = "chf"
    monkeypatch.setattr("django_core_micha.billing.contracts.load_billing_config", lambda: p.cfg)
    account = TokenAccount.objects.create(user=p.buyers[0])
    TokenEntry.objects.create(account=account, kind="purchase", amount=100, remaining=100,
                              reference="unrelated-token", app="cinema")
    attempt = start(p.buyers[0])
    meta = p.sessions[attempt.stripe_session_id]["metadata"]
    now = int(timezone.now().timestamp())
    def invoice(identifier, start_time, reason):
        return {"id": identifier, "subscription": "sub_object", "status": "paid",
                "period_start": start_time, "period_end": start_time + 3600,
                "created": start_time, "amount_paid": 1000, "currency": "chf",
                "payment_intent": "pi_" + identifier, "billing_reason": reason}
    initial = invoice("initial", now - 7200, "subscription_create")
    renewal = invoice("renewal", now - 3600, "subscription_cycle")
    remote = {"id": "sub_object", "customer": attempt.stripe_customer_id,
              "metadata": meta, "status": "active"}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(
        Subscription=SimpleNamespace(retrieve=lambda sid: deepcopy(remote)),
        Invoice=SimpleNamespace(list=lambda **kwargs: SimpleNamespace(data=[deepcopy(initial)])),
    ))
    barrier = Barrier(3)
    def apply(event_invoice):
        barrier.wait(timeout=10)
        return handle_object_event("invoice.paid", deepcopy(event_invoice), meta, {})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(worker, apply, item) for item in (renewal, initial, renewal)]
        assert all(f.result(timeout=15) for f in futures)
    subscription = ObjectSubscription.objects.get()
    assert int(subscription.current_period_start.timestamp()) == now - 3600
    assert subscription.subject_id == attempt.subject_id
    assert Purchase.objects.filter(kind="object_subscription", stripe_object_id="sub_object").count() == 1
    assert set(ContractPayment.objects.values_list("stripe_invoice_id", flat=True)) == {"initial", "renewal"}
    assert ContractPayment.objects.count() == 2
    assert token_balance(p.buyers[0]) == 100
    assert TokenEntry.objects.filter(account=account).count() == 1
    assert len(p.calls) == 1


def test_competing_first_buyers_create_one_subject_and_session(provider, monkeypatch):
    p = provider
    barrier = Barrier(2)
    original = ObjectBillingSubject.objects.get_or_create
    def simultaneous(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(ObjectBillingSubject.objects, "get_or_create", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, start, buyer, "first", key * 16)
                   for buyer, key in zip(p.buyers, ("a", "b"))]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=15))
            except ObjectCheckoutConflict:
                outcomes.append(None)
    assert sum(x is not None for x in outcomes) == 1
    assert ObjectBillingSubject.objects.filter(app="cinema", object_key="first").count() == 1
    assert ObjectCheckoutAttempt.objects.count() == len(p.calls) == 1
