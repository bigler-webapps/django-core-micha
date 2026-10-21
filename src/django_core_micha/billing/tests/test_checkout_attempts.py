import json
from types import SimpleNamespace

import pytest
import yaml
from urllib.parse import parse_qs, urlsplit
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing import stripe_service
from django_core_micha.billing.models import CheckoutAttempt, StripePrice
from django_core_micha.billing.views import checkout, checkout_attempt, me

APP = "maana"
CONTEXTS = {"setup": {"success_url": "https://app.test/done?from=wizard#top", "cancel_url": "https://app.test/return"},
            "account": {"success_url": "https://app.test/account/done", "cancel_url": "https://app.test/account"}}


def make_user(name):
    return get_user_model().objects.create_user(username=name, email=f"{name}@example.test", password="secret")


def plan_file(tmp_path):
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": APP, "currency": "usd", "plans": [
        {"key": "monthly", "kind": "subscription", "name": "Monthly", "amount_cents": 1500,
         "interval": "month", "tax_code": "txcd_10103001"}], "token_rates": {}}), encoding="utf-8")
    StripePrice.objects.create(app=APP, plan_key="monthly", version=1, lookup_key="maana:monthly:v1", fingerprint="x",
                               stripe_product_id="prod_1", stripe_price_id="price_1")
    return path


def req(user, body):
    request = RequestFactory().post("/checkout/", data=json.dumps(body), content_type="application/json")
    request.user = user
    request._dont_enforce_csrf_checks = True
    return request


@pytest.mark.django_db
def test_context_attempt_replay_uses_same_stripe_session_and_allowlisted_destinations(monkeypatch, tmp_path):
    user = make_user("checkout-replay")
    path = plan_file(tmp_path)
    calls = []
    fake = SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_1")),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=lambda **kw: calls.append(kw) or SimpleNamespace(
            id="cs_1", url="https://stripe.test/session", expires_at=int(timezone.now().timestamp()) + 3600))))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    data = {"plan_key": "monthly", "context": "setup", "idempotency_key": "1234567890abcdef"}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        first = checkout(req(user, data)); second = checkout(req(user, data))
        a, b = json.loads(first.content), json.loads(second.content)
        assert first.status_code == second.status_code == 200
        assert a["attempt"]["reference"] == b["attempt"]["reference"]
        assert a["url"] == b["url"] == "https://stripe.test/session"
        assert len(calls) == 1 and calls[0]["idempotency_key"] == f"dcm-checkout:{APP}:{a['attempt']['reference']}"
        assert urlsplit(calls[0]["success_url"]).fragment == "top"
        assert parse_qs(urlsplit(calls[0]["success_url"]).query) == {"from": ["wizard"], "checkout_attempt": [a["attempt"]["reference"]]}
        assert calls[0]["metadata"]["checkout_context"] == "setup"
        assert calls[0]["client_reference_id"] == a["attempt"]["reference"]
        attempt = CheckoutAttempt.objects.get()
        anonymous_request = RequestFactory().get("/")
        anonymous_request.user = type("Anonymous", (), {"is_authenticated": False})()
        resolution = checkout_attempt(anonymous_request, attempt.reference)
        assert resolution.status_code == 401
        get = RequestFactory().get("/"); get.user = user
        resolved = checkout_attempt(get, attempt.reference)
        assert resolved.status_code == 200 and json.loads(resolved.content)["url"] == "https://stripe.test/session"
        assert resolved["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_stripe_failure_keeps_attempt_and_same_key_retries_same_reference(monkeypatch, tmp_path):
    user = make_user("checkout-retry")
    path = plan_file(tmp_path)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1: raise RuntimeError("network interruption")
        return SimpleNamespace(id="cs_retry", url="https://stripe.test/retry", expires_at=None)
    fake = SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_retry")),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=create)))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    data = {"plan_key": "monthly", "context": "setup", "idempotency_key": "retry-key-12345678"}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        first = checkout(req(user, data))
        attempt = CheckoutAttempt.objects.get()
        assert first.status_code == 502 and attempt.stripe_session_id == ""
        second = checkout(req(user, data))
        attempt.refresh_from_db()
        assert second.status_code == 200 and len(calls) == 2
        assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
        assert attempt.stripe_session_id == "cs_retry"


@pytest.mark.django_db
def test_unknown_context_hostile_urls_and_foreign_references_fail(monkeypatch, tmp_path):
    user, other = make_user("context-owner"), make_user("context-other")
    path = plan_file(tmp_path)
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path,
                          DCM_BILLING_CHECKOUT_CONTEXTS={"setup": {"success_url": "https://evil.test/?checkout_attempt=x", "cancel_url": "javascript:alert(1)"}}):
        response = checkout(req(user, {"plan_key": "monthly", "context": "setup", "idempotency_key": "1234567890abcdef"}))
        assert response.status_code == 400 and json.loads(response.content)["detail"] == "unknown_context"
    attempt = CheckoutAttempt.objects.create(reference="opaque-reference-123456789", user=user, app=APP,
        plan_key="monthly", context="setup", idempotency_key="1234567890abcdef", stripe_price_id="price_1")
    request = RequestFactory().get("/"); request.user = other
    with override_settings(DCM_BILLING_APP=APP):
        response = checkout_attempt(request, attempt.reference)
    assert response.status_code == 404 and json.loads(response.content)["detail"] == "attempt_not_found"


@pytest.mark.django_db
def test_explicit_context_errors_and_key_reuse_are_owned_and_ordered(tmp_path):
    user = make_user("attempt-errors")
    path = plan_file(tmp_path)
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        assert json.loads(checkout(req(user, {"context": "missing"})).content)["detail"] == "unknown_context"
        assert json.loads(checkout(req(user, {"context": "setup"})).content)["detail"] == "invalid_idempotency_key"
        base = {"context": "setup", "idempotency_key": "1234567890abcdef", "plan_key": "monthly"}
        row = CheckoutAttempt.objects.create(reference="opaque-reference-987654321", user=user, app=APP, plan_key="monthly",
            context="setup", idempotency_key=base["idempotency_key"], stripe_price_id="price_1", status="failed")
        replay = checkout(req(user, base))
        assert replay.status_code == 409 and json.loads(replay.content)["detail"] == "attempt_not_open"
        changed = checkout(req(user, {**base, "context": "account"}))
        assert changed.status_code == 409 and json.loads(changed.content)["detail"] == "idempotency_key_reused"


@pytest.mark.django_db
def test_forged_success_return_does_not_change_attempt_or_entitlement(tmp_path):
    user = make_user("forged-return")
    path = plan_file(tmp_path)
    attempt = CheckoutAttempt.objects.create(reference="opaque-return-123456789", user=user, app=APP,
        plan_key="monthly", context="setup", idempotency_key="1234567890abcdef", stripe_price_id="price_1")
    request = RequestFactory().get("/?success=true&checkout_attempt=" + attempt.reference); request.user = user
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path):
        response = checkout_attempt(request, attempt.reference)
        state = json.loads(me(request).content)
    attempt.refresh_from_db()
    assert response.status_code == 200 and attempt.status == "open"
    assert state["access"]["status"] == "read_only"
    assert state["tokens"]["balance"] == 0


@pytest.mark.django_db
def test_webhook_attempt_transitions_are_forward_only_and_failure_events_are_handled(tmp_path):
    user = make_user("attempt-webhook")
    path = plan_file(tmp_path)
    attempt = CheckoutAttempt.objects.create(reference="opaque-webhook-12345678", user=user, app=APP,
        plan_key="monthly", context="setup", idempotency_key="1234567890abcdef", stripe_price_id="price_1")
    base = {"id": "cs_webhook", "metadata": {"app": APP, "plan_key": "monthly", "user_id": str(user.pk), "checkout_attempt": attempt.reference}}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path):
        assert stripe_service.process_stripe_event({"type": "checkout.session.completed", "data": {"object": {**base, "payment_status": "unpaid"}}}) == "processed"
        attempt.refresh_from_db(); assert attempt.status == "processing" and attempt.stripe_session_id == "cs_webhook"
        assert stripe_service.process_stripe_event({"type": "checkout.session.async_payment_failed", "data": {"object": base}}) == "processed"
        attempt.refresh_from_db(); assert attempt.status == "failed"
        stripe_service.process_stripe_event({"type": "checkout.session.async_payment_succeeded", "data": {"object": {**base, "payment_status": "paid"}}})
        attempt.refresh_from_db(); assert attempt.status == "failed"


def counting_stripe(calls):
    def create(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        return SimpleNamespace(id=f"cs_{n}", url=f"https://stripe.test/{n}", expires_at=int(timezone.now().timestamp()) + 3600)
    return SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_n")),
                           checkout=SimpleNamespace(Session=SimpleNamespace(create=create)))


@pytest.mark.django_db
@pytest.mark.parametrize("extra", [{}, {"context": None}])
def test_checkout_without_context_is_unchanged(monkeypatch, tmp_path, extra):
    user = make_user("legacy-checkout")
    path = plan_file(tmp_path)
    calls = []
    monkeypatch.setattr(stripe_service, "_stripe", lambda: counting_stripe(calls))
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS,
                           DCM_BILLING_SUCCESS_URL="https://app.test/ok", DCM_BILLING_CANCEL_URL="https://app.test/no"):
        response = checkout(req(user, {"plan_key": "monthly", "idempotency_key": "1234567890abcdef", **extra}))
    assert response.status_code == 200 and json.loads(response.content) == {"url": "https://stripe.test/1"}
    sent = calls[0]
    assert "idempotency_key" not in sent and "client_reference_id" not in sent
    assert (sent["success_url"], sent["cancel_url"]) == ("https://app.test/ok", "https://app.test/no")
    assert set(sent["metadata"]) == {"app", "plan_key", "user_id"}
    assert not CheckoutAttempt.objects.exists()


@pytest.mark.django_db
def test_setup_and_account_attempts_stay_distinct_and_a_failed_attempt_is_retried_with_a_new_key(monkeypatch, tmp_path):
    user = make_user("attempt-distinct")
    path = plan_file(tmp_path)
    calls = []
    monkeypatch.setattr(stripe_service, "_stripe", lambda: counting_stripe(calls))
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        setup = json.loads(checkout(req(user, {"plan_key": "monthly", "context": "setup", "idempotency_key": "setup-key-0000001"})).content)
        account = json.loads(checkout(req(user, {"plan_key": "monthly", "context": "account", "idempotency_key": "account-key-00001"})).content)
        assert setup["attempt"]["reference"] != account["attempt"]["reference"] and len(calls) == 2
        assert calls[1]["success_url"].startswith("https://app.test/account/done?checkout_attempt=")
        for body, context in ((setup, "setup"), (account, "account")):
            get = RequestFactory().get("/"); get.user = user
            assert json.loads(checkout_attempt(get, body["attempt"]["reference"]).content)["attempt"]["context"] == context
        failed = {"id": "cs_1", "metadata": {"app": APP, "plan_key": "monthly", "user_id": str(user.pk)}}
        assert stripe_service.process_stripe_event({"type": "checkout.session.async_payment_failed", "data": {"object": failed}}) == "processed"
        replay = checkout(req(user, {"plan_key": "monthly", "context": "setup", "idempotency_key": "setup-key-0000001"}))
        assert replay.status_code == 409 and json.loads(replay.content)["attempt"]["status"] == "failed" and len(calls) == 2
        retry = json.loads(checkout(req(user, {"plan_key": "monthly", "context": "setup", "idempotency_key": "setup-key-0000002"})).content)
        assert retry["attempt"]["status"] == "open" and retry["attempt"]["reference"] != setup["attempt"]["reference"] and len(calls) == 3


@pytest.mark.django_db
def test_repeated_and_delayed_webhooks_grant_once_and_never_move_an_attempt_back(tmp_path):
    from django_core_micha.billing.models import Purchase
    from django_core_micha.billing.services import token_balance
    user = make_user("attempt-grant")
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": APP, "currency": "usd", "plans": [
        {"key": "tokens_100", "kind": "token_package", "name": "100", "amount_cents": 500, "tokens": 100, "tax_code": "txcd_10103001"}],
        "token_rates": {}}), encoding="utf-8")
    attempt = CheckoutAttempt.objects.create(reference="opaque-grant-1234567890", user=user, app=APP, plan_key="tokens_100",
        context="setup", idempotency_key="1234567890abcdef", stripe_price_id="price_t", stripe_session_id="cs_tok")
    session = {"id": "cs_tok", "payment_intent": "pi_tok", "amount_total": 500, "currency": "usd",
               "metadata": {"app": APP, "plan_key": "tokens_100", "user_id": str(user.pk), "checkout_attempt": attempt.reference}}
    event = lambda kind, status: {"type": kind, "data": {"object": {**session, "payment_status": status}}}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=str(path)):
        stripe_service.process_stripe_event(event("checkout.session.completed", "unpaid"))
        attempt.refresh_from_db(); assert attempt.status == "processing" and token_balance(user) == 0
        for _ in range(2):
            stripe_service.process_stripe_event(event("checkout.session.async_payment_succeeded", "paid"))
        stripe_service.process_stripe_event(event("checkout.session.completed", "unpaid"))
        stripe_service.process_stripe_event(event("checkout.session.expired", "unpaid"))
    attempt.refresh_from_db()
    assert attempt.status == "paid" and token_balance(user) == 100 and Purchase.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_resolution_hides_other_apps_and_reports_a_lapsed_session_as_expired(tmp_path):
    from datetime import timedelta
    user = make_user("attempt-expired")
    lapsed = CheckoutAttempt.objects.create(reference="opaque-lapsed-1234567890", user=user, app=APP, plan_key="monthly",
        context="setup", idempotency_key="lapsed-key-000001", stripe_price_id="price_1", stripe_session_id="cs_lapsed",
        stripe_checkout_url="https://stripe.test/lapsed", expires_at=timezone.now() - timedelta(minutes=1))
    foreign = CheckoutAttempt.objects.create(reference="opaque-foreign-app-12345", user=user, app="other",
        plan_key="monthly", context="setup", idempotency_key="foreign-key-00001", stripe_price_id="price_1")
    get = RequestFactory().get("/"); get.user = user
    with override_settings(DCM_BILLING_APP=APP):
        body = json.loads(checkout_attempt(get, lapsed.reference).content)
        assert body["attempt"]["status"] == "expired" and body["url"] is None
        assert checkout_attempt(get, foreign.reference).status_code == 404


@pytest.mark.django_db
def test_a_concurrent_first_request_with_the_same_key_reuses_its_attempt_and_session(monkeypatch, tmp_path):
    from datetime import timedelta
    from django.db import IntegrityError, transaction
    from django_core_micha.billing import views
    user = make_user("attempt-race")
    path = plan_file(tmp_path)
    calls = []
    monkeypatch.setattr(stripe_service, "_stripe", lambda: counting_stripe(calls))
    # the attempt a parallel request with the same key committed, session already stored
    winner = CheckoutAttempt.objects.create(reference="opaque-race-winner-123456", user=user, app=APP, plan_key="monthly",
        context="setup", idempotency_key="race-key-00000001", stripe_price_id="price_1", stripe_session_id="cs_winner",
        stripe_checkout_url="https://stripe.test/winner", expires_at=timezone.now() + timedelta(hours=1))
    with pytest.raises(IntegrityError), transaction.atomic():
        CheckoutAttempt.objects.create(reference="opaque-race-loser-1234567", user=user, app=APP, plan_key="monthly",
            context="setup", idempotency_key="race-key-00000001", stripe_price_id="price_1")
    original_filter = views.CheckoutAttempt.objects.filter
    misses = iter([True])
    def filter_missing_once(*args, **kwargs):
        # this request's own lookup ran before the parallel insert became visible
        return views.CheckoutAttempt.objects.none() if next(misses, False) else original_filter(*args, **kwargs)
    monkeypatch.setattr(views.CheckoutAttempt.objects, "filter", filter_missing_once)
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        body = json.loads(checkout(req(user, {"plan_key": "monthly", "context": "setup", "idempotency_key": "race-key-00000001"})).content)
    assert body["attempt"]["reference"] == winner.reference and body["url"] == "https://stripe.test/winner"
    assert calls == [] and CheckoutAttempt.objects.count() == 1


@pytest.mark.django_db
def test_a_replay_repeats_the_stored_price_after_the_plan_was_resynced(monkeypatch, tmp_path):
    user = make_user("attempt-stored-price")
    path = plan_file(tmp_path)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1: raise RuntimeError("timeout")
        return SimpleNamespace(id="cs_price", url="https://stripe.test/price", expires_at=None)
    monkeypatch.setattr(stripe_service, "_stripe", lambda: SimpleNamespace(Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_p")),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=create))))
    data = {"plan_key": "monthly", "context": "setup", "idempotency_key": "price-key-0000001"}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        assert checkout(req(user, data)).status_code == 502
        StripePrice.objects.all().delete()  # the price behind the first request is no longer the current one
        assert checkout(req(user, data)).status_code == 200
        assert checkout(req(user, {**data, "idempotency_key": "price-key-0000002"})).status_code == 400
    assert [c["line_items"][0]["price"] for c in calls] == ["price_1", "price_1"]
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]


@pytest.mark.django_db
def test_an_unresolvable_or_failing_completion_leaves_the_attempt_open(monkeypatch, tmp_path):
    from django.db import transaction
    user = make_user("attempt-unresolved")
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": APP, "currency": "usd", "plans": [
        {"key": "tokens_100", "kind": "token_package", "name": "100", "amount_cents": 500, "tokens": 100, "tax_code": "txcd_10103001"}],
        "token_rates": {}}), encoding="utf-8")
    attempt = CheckoutAttempt.objects.create(reference="opaque-unresolved-123456", user=user, app=APP, plan_key="tokens_100",
        context="setup", idempotency_key="1234567890abcdef", stripe_price_id="price_t", stripe_session_id="cs_un")
    session = {"id": "cs_un", "payment_status": "paid", "payment_intent": "pi_un",
               "metadata": {"app": APP, "plan_key": "removed_plan", "user_id": str(user.pk)}}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=str(path)):
        assert stripe_service.process_stripe_event({"type": "checkout.session.completed", "data": {"object": session}}) == "ignored"
        attempt.refresh_from_db(); assert attempt.status == "open"
        def failing_credit(*args, **kwargs): raise RuntimeError("ledger unavailable")
        monkeypatch.setattr(stripe_service, "credit_purchase", failing_credit)
        paid = {**session, "metadata": {**session["metadata"], "plan_key": "tokens_100"}}
        with pytest.raises(RuntimeError), transaction.atomic():  # the webhook's own transaction
            stripe_service.process_stripe_event({"type": "checkout.session.completed", "data": {"object": paid}})
    attempt.refresh_from_db()
    assert attempt.status == "open"


@pytest.mark.django_db
def test_destinations_come_from_the_configured_context_only(monkeypatch, tmp_path):
    user = make_user("attempt-destinations")
    path = plan_file(tmp_path)
    calls = []
    monkeypatch.setattr(stripe_service, "_stripe", lambda: counting_stripe(calls))
    other = {"onboarding": {"success_url": "http://localhost:5173/wizard/paid?step=3", "cancel_url": "https://shop.example/back"}}
    body = {"plan_key": "monthly", "context": "onboarding", "idempotency_key": "destination-key-01",
            "success_url": "https://evil.test/", "cancel_url": "https://evil.test/"}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=other,
                           DCM_BILLING_SUCCESS_URL="https://app.test/ok", DCM_BILLING_CANCEL_URL="https://app.test/no"):
        reference = json.loads(checkout(req(user, body)).content)["attempt"]["reference"]
    assert calls[0]["success_url"] == f"http://localhost:5173/wizard/paid?step=3&checkout_attempt={reference}"
    assert calls[0]["cancel_url"] == f"https://shop.example/back?checkout_attempt={reference}"


@pytest.mark.django_db
def test_an_event_for_another_plan_or_context_never_moves_the_attempt(tmp_path):
    user = make_user("attempt-mismatch")
    path = plan_file(tmp_path)
    attempt = CheckoutAttempt.objects.create(reference="opaque-mismatch-12345678", user=user, app=APP, plan_key="monthly",
        context="setup", idempotency_key="1234567890abcdef", stripe_price_id="price_1")
    meta = {"app": APP, "plan_key": "monthly", "user_id": str(user.pk), "checkout_attempt": attempt.reference}
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path):
        other = make_user("attempt-mismatch-other")
        for changed in ({"plan_key": "other_plan"}, {"checkout_context": "account"}, {"user_id": str(other.pk)}):
            event = {"type": "checkout.session.expired", "data": {"object": {"id": "cs_other", "metadata": {**meta, **changed}}}}
            assert stripe_service.process_stripe_event(event) == "ignored"
    attempt.refresh_from_db()
    assert attempt.status == "open" and attempt.stripe_session_id == ""


@pytest.mark.django_db
def test_contextual_checkout_requires_a_session_user_and_the_csrf_token(monkeypatch, tmp_path):
    user = make_user("attempt-csrf")
    path = plan_file(tmp_path)
    calls = []
    monkeypatch.setattr(stripe_service, "_stripe", lambda: counting_stripe(calls))
    body = json.dumps({"plan_key": "monthly", "context": "setup", "idempotency_key": "csrf-key-00000001"})
    with override_settings(DCM_BILLING_APP=APP, DCM_BILLING_FILE=path, DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        anonymous = req(type("Anonymous", (), {"is_authenticated": False})(), json.loads(body))
        response = checkout(anonymous)
        assert response.status_code == 401 and json.loads(response.content)["detail"] == "authentication_required"
        no_token = RequestFactory().post("/checkout/", data=body, content_type="application/json")
        no_token.user = user
        no_token._dont_enforce_csrf_checks = False  # the billing conftest exempts factory requests by default
        assert checkout(no_token).status_code == 403
    assert calls == [] and not CheckoutAttempt.objects.exists()


def test_malformed_contexts_are_a_system_check_error():
    from django.core import checks
    hostile = {"setup": {"success_url": "https://app.test/?checkout_attempt=x", "cancel_url": "javascript:alert(1)"}}
    with override_settings(DCM_BILLING_CHECKOUT_CONTEXTS=hostile):
        assert "dcm_billing.E002" in {message.id for message in checks.run_checks()}
    with override_settings(DCM_BILLING_CHECKOUT_CONTEXTS=CONTEXTS):
        assert "dcm_billing.E002" not in {message.id for message in checks.run_checks()}


@pytest.mark.django_db(transaction=True)
def test_checkout_attempt_migration_preserves_existing_billing_rows():
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    user = make_user("migration-user")
    executor = MigrationExecutor(connection)
    executor.migrate([("dcm_billing", "0002_purchase_withdrawal")])
    old_apps = executor.loader.project_state([("dcm_billing", "0002_purchase_withdrawal")]).apps
    HistoricalUser = old_apps.get_model("auth", "User")
    HistoricalPrice = old_apps.get_model("dcm_billing", "StripePrice")
    HistoricalPrice.objects.create(app=APP, plan_key="x", version=1, lookup_key="old:price", fingerprint="old", stripe_product_id="p", stripe_price_id="sp")
    HistoricalUser.objects.filter(pk=user.pk).update(first_name="Preserved")
    try:
        MigrationExecutor(connection).migrate([("dcm_billing", "0003_checkoutattempt")])
        user.refresh_from_db()
        assert user.first_name == "Preserved"
        assert StripePrice.objects.get(lookup_key="old:price").stripe_price_id == "sp"
        assert CheckoutAttempt.objects.count() == 0
    finally:
        call_command("migrate", run_syncdb=True, verbosity=0)
