from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, connection
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing.models import Purchase, StripeEvent, Subscription, TokenEntry
from django_core_micha.billing.services import access_state, grant_tokens
from django_core_micha.billing.stripe_service import process_stripe_event
from django_core_micha.billing.views import webhook
from django_core_micha.billing.withdrawal import withdraw, withdrawal_key


pytestmark = pytest.mark.django_db


def user(i):
    return get_user_model().objects.create_user(username=f"refund-event-{i}", email=f"refund-event-{i}@example.com", password="x")


def purchase(u, *, with_subscription=True, **kw):
    now = timezone.now()
    defaults = dict(kind="subscription", plan_key="monthly", stripe_object_id=f"sub-{u.pk}",
        stripe_payment_intent_id=f"pi-{u.pk}", amount_paid_cents=1000, currency="eur", billing_country="DE",
        purchased_at=now - timedelta(days=1), term_start=now - timedelta(days=1), term_end=now + timedelta(days=29),
        grant_references=[], tokens_granted=0)
    defaults.update(kw)
    row = Purchase.objects.create(user=u, **defaults)
    if with_subscription:
        Subscription.objects.create(user=u, plan_key=row.plan_key, stripe_subscription_id=row.stripe_object_id,
            status="active", current_period_start=now - timedelta(days=1), current_period_end=now + timedelta(days=29),
            cancel_at_period_end=True)
    return row


def refund_for(p, **changes):
    data = {"id": "re-withdrawal", "status": "succeeded", "amount": 700, "currency": p.currency,
        "created": int(timezone.now().timestamp()), "reason": "requested_by_customer",
        "payment_intent": p.stripe_payment_intent_id,
        "metadata": {"app": "app", "purchase_id": str(p.pk), "dcm_withdrawal": withdrawal_key("app", p.pk)}}
    data.update(changes)
    return data


def event(refund, *, event_id="evt-refund", kind="refund.created"):
    return {"id": event_id, "type": kind, "created": int(timezone.now().timestamp()), "data": {"object": refund}}


def fake_stripe(monkeypatch, *, refunds=None):
    calls = {"create": [], "cancel": [], "list": []}
    def create(**kwargs):
        calls["create"].append(kwargs)
        return {"id": "re-post"}
    def cancel(sid):
        calls["cancel"].append(sid)
        return {"id": sid}
    def list_refunds(**kwargs):
        calls["list"].append(kwargs)
        return SimpleNamespace(data=list(refunds or []))
    stripe = SimpleNamespace(Refund=SimpleNamespace(create=create, list=list_refunds),
        Subscription=SimpleNamespace(cancel=cancel))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    return calls, stripe


def assert_untouched(p):
    p.refresh_from_db()
    sub = Subscription.objects.get(stripe_subscription_id=p.stripe_object_id)
    assert p.status == "active" and not p.withdrawn_at and not p.withdrawal_email_sent and not p.stripe_refund_id
    assert sub.status == "active" and sub.cancel_at_period_end
    assert not TokenEntry.objects.filter(kind="expiry", reference__startswith=f"withdrawal:{p.pk}:").exists()
    assert len(mail.outbox) == 0


def _prepare_tokens(u, p):
    grant_tokens(u, 12, reference=f"grant-{p.pk}", purpose="term")
    p.grant_references = [f"grant-{p.pk}"]
    p.tokens_granted = 12
    p.save(update_fields=("grant_references", "tokens_granted"))


def test_crash_after_stripe_refund_is_completed_by_webhook(monkeypatch, django_capture_on_commit_callbacks):
    u = user(1); p = purchase(u); _prepare_tokens(u, p)
    calls, _ = fake_stripe(monkeypatch)
    refund = refund_for(p)
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        assert process_stripe_event(event(refund)) == "processed"
    p.refresh_from_db()
    sub = Subscription.objects.get(stripe_subscription_id=p.stripe_object_id)
    grant = TokenEntry.objects.get(reference=f"grant-{p.pk}")
    assert p.status == "withdrawn" and p.refund_cents == 700 and p.stripe_refund_id == "re-withdrawal"
    assert p.withdrawn_at and sub.status == "canceled" and not sub.cancel_at_period_end
    assert access_state(u).status == "read_only" and grant.remaining == 0
    assert TokenEntry.objects.filter(kind="expiry", reference=f"withdrawal:{p.pk}:{grant.pk}").count() == 1
    assert len(mail.outbox) == 1 and calls["create"] == []


def test_webhook_first_then_post_returns_stored_result_without_refunding_again(monkeypatch, django_capture_on_commit_callbacks):
    u = user(2); p = purchase(u); calls, _ = fake_stripe(monkeypatch)
    with override_settings(DCM_BILLING_APP="app", DCM_BILLING_WITHDRAWAL_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        process_stripe_event(event(refund_for(p)))
        calc, already = withdraw(p)
    assert already and calc.refund_cents == 700 and calls["create"] == [] and len(mail.outbox) == 1


def test_post_first_then_webhook_does_not_duplicate_completion(monkeypatch, django_capture_on_commit_callbacks):
    u = user(3); p = purchase(u); _prepare_tokens(u, p)
    calls, _ = fake_stripe(monkeypatch)
    with override_settings(DCM_BILLING_APP="app", DCM_BILLING_WITHDRAWAL_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        withdraw(p)
        before = TokenEntry.objects.filter(kind="expiry", reference__startswith=f"withdrawal:{p.pk}:").count()
        process_stripe_event(event(refund_for(p)))
    assert calls["create"] and len(mail.outbox) == 1
    assert TokenEntry.objects.filter(kind="expiry", reference__startswith=f"withdrawal:{p.pk}:").count() == before


@pytest.mark.django_db(transaction=True)
def test_concurrent_post_and_webhook_complete_once(monkeypatch, django_capture_on_commit_callbacks):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level SELECT FOR UPDATE locking")
    u = user(4); p = purchase(u); _prepare_tokens(u, p)
    barrier = Barrier(2)
    fake_stripe(monkeypatch)
    def post_path():
        close_old_connections()
        try:
            barrier.wait()
            return withdraw(Purchase.objects.get(pk=p.pk))[1]
        finally:
            close_old_connections()
    def webhook_path():
        close_old_connections()
        try:
            barrier.wait()
            return process_stripe_event(event(refund_for(p)))
        finally:
            close_old_connections()
    with override_settings(DCM_BILLING_APP="app", DCM_BILLING_WITHDRAWAL_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        with ThreadPoolExecutor(max_workers=2) as pool:
            post_future = pool.submit(post_path)
            webhook_future = pool.submit(webhook_path)
            post_future.result(timeout=20)
            assert webhook_future.result(timeout=20) == "processed"
    p.refresh_from_db()
    assert p.status == "withdrawn" and TokenEntry.objects.filter(kind="expiry", reference__startswith=f"withdrawal:{p.pk}:").count() == 1
    assert len(mail.outbox) == 1


@pytest.mark.parametrize("metadata,reason", [
    ({"app": "app", "purchase_id": "1"}, "manual"),
    ({"app": "other", "purchase_id": "1", "dcm_withdrawal": "dcm-withdrawal:other:1"}, "requested_by_customer"),
    ({"app": "app", "purchase_id": "not-an-int", "dcm_withdrawal": "dcm-withdrawal:app:not-an-int"}, "requested_by_customer"),
])
def test_unrecognized_refund_metadata_does_not_end_access(monkeypatch, metadata, reason):
    u = user(5); p = purchase(u); fake_stripe(monkeypatch)
    refund = refund_for(p, metadata=metadata, reason=reason)
    with override_settings(DCM_BILLING_APP="app"):
        process_stripe_event(event(refund))
    assert_untouched(p)


@pytest.mark.parametrize("changes", [
    {"payment_intent": "pi-wrong"}, {"amount": 1001}, {"status": "pending"}, {"amount": 0},
    {"currency": "usd"}, {"currency": ""}, {"amount": 700.5}, {"amount": True},
])
def test_invalid_refund_details_are_refused(monkeypatch, changes):
    u = user(6); p = purchase(u); fake_stripe(monkeypatch)
    with override_settings(DCM_BILLING_APP="app"):
        process_stripe_event(event(refund_for(p, **changes)))
    assert_untouched(p)


def test_refund_created_before_the_metadata_key_is_found_by_the_request_idempotency_key(monkeypatch, django_capture_on_commit_callbacks):
    u = user(7); p = purchase(u); fake_stripe(monkeypatch)
    refund = refund_for(p, metadata={"app": "app", "purchase_id": str(p.pk)})  # a 3.5.0/3.6.0 refund: no dcm_withdrawal metadata
    ev = event(refund); ev["request"] = {"id": "req_1", "idempotency_key": withdrawal_key("app", p.pk)}
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        process_stripe_event(ev)
    p.refresh_from_db()
    assert p.status == "withdrawn" and p.stripe_refund_id == "re-withdrawal" and len(mail.outbox) == 1


@pytest.mark.parametrize("request_info", [None, {"idempotency_key": "manual-dashboard-key"}, {"idempotency_key": "dcm-withdrawal:other:1"}])
def test_refund_without_our_key_anywhere_is_not_a_withdrawal(monkeypatch, request_info):
    u = user(12); p = purchase(u); fake_stripe(monkeypatch)
    refund = refund_for(p, metadata={"app": "app", "purchase_id": str(p.pk)}, reason="requested_by_customer")
    ev = event(refund)
    if request_info: ev["request"] = request_info
    with override_settings(DCM_BILLING_APP="app"):
        process_stripe_event(ev)
    assert_untouched(p)


def test_charge_refunded_avoids_api_without_active_purchase_and_lists_when_active(monkeypatch, django_capture_on_commit_callbacks):
    u = user(8); p = purchase(u); _prepare_tokens(u, p); calls, _ = fake_stripe(monkeypatch, refunds=[refund_for(p)])
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        p.status = "withdrawn"; p.save(update_fields=("status",))
        process_stripe_event({"type": "charge.refunded", "data": {"object": {"id": "ch-1", "payment_intent": p.stripe_payment_intent_id}}})
        assert calls["list"] == []
        p.status = "active"; p.save(update_fields=("status",))
        process_stripe_event({"type": "charge.refunded", "data": {"object": {"id": "ch-1", "payment_intent": p.stripe_payment_intent_id}}})
    p.refresh_from_db()
    assert calls["list"] == [{"charge": "ch-1", "limit": 100}] and p.status == "withdrawn"
    assert calls["cancel"] == [p.stripe_object_id] and Subscription.objects.get(stripe_subscription_id=p.stripe_object_id).status == "canceled"
    assert TokenEntry.objects.get(reference=f"grant-{p.pk}").remaining == 0 and len(mail.outbox) == 1


def test_reconciliation_completes_pending_withdrawal_from_refund_list(monkeypatch, django_capture_on_commit_callbacks):
    from django.core.management import call_command
    u = user(9); p = purchase(u); _prepare_tokens(u, p); refund = refund_for(p)
    class Page:
        def __init__(self, data): self.data = data
        def auto_paging_iter(self): return iter(self.data)
    stripe = SimpleNamespace(Subscription=SimpleNamespace(list=lambda **kw: Page([]), cancel=lambda sid: {"id": sid}),
        Event=SimpleNamespace(list=lambda **kw: Page([])), Refund=SimpleNamespace(list=lambda **kw: Page([refund])))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    monkeypatch.setattr("django_core_micha.billing.management.commands.billing_reconcile._stripe", lambda: stripe)
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        from io import StringIO
        out = StringIO()
        call_command("billing_reconcile", stdout=out)
    p.refresh_from_db()
    assert p.status == "withdrawn" and out.getvalue().strip() == "1"
    assert Subscription.objects.get(stripe_subscription_id=p.stripe_object_id).status == "canceled"
    assert TokenEntry.objects.get(reference=f"grant-{p.pk}").remaining == 0 and len(mail.outbox) == 1


def test_replayed_webhook_event_id_completes_once(monkeypatch, django_capture_on_commit_callbacks):
    u = user(10); p = purchase(u); fake_stripe(monkeypatch)
    refund = refund_for(p)
    stripe = SimpleNamespace(Webhook=SimpleNamespace(construct_event=lambda *args: event(refund, event_id="evt-replay")),
        Refund=SimpleNamespace(create=lambda **kw: {"id": "re-post"}),
        Subscription=SimpleNamespace(cancel=lambda sid: {"id": sid}))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    request = RequestFactory().post("/stripe/webhook/", data=b"{}", content_type="application/json")
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        assert webhook(request).status_code == 200
        assert webhook(request).status_code == 200
    p.refresh_from_db()
    assert p.status == "withdrawn" and StripeEvent.objects.filter(event_id="evt-replay", status="processed").count() == 1
    assert len(mail.outbox) == 1


def test_post_response_after_a_webhook_completion_reports_the_stored_result(monkeypatch, django_capture_on_commit_callbacks):
    from django_core_micha.billing.views import withdrawal_execute
    import json
    u = user(11); p = purchase(u); fake_stripe(monkeypatch)
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        process_stripe_event(event(refund_for(p, amount=650)))
        request = RequestFactory().post("/withdrawal/", data=json.dumps({"confirm": True}), content_type="application/json"); request.user = u
        body = json.loads(withdrawal_execute(request, p.pk).content)
    assert body["already_withdrawn"] is True and body["refund_cents"] == 650 and body["calculation"]
