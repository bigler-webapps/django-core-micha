import hashlib
import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing import stripe_service
from django_core_micha.billing.models import AICostRecord, TeamPackage, TokenEntry
from django_core_micha.billing.services import debit_tokens, grant_tokens, record_ai_call
from django_core_micha.billing.views import checkout, cost_report

pytestmark = pytest.mark.django_db


def _user(email):
    return get_user_model().objects.create_user(username=email, email=email)


def test_cost_report_and_debits_are_scoped_to_the_configured_app():
    user = _user("scope@example.com")
    grant_tokens(user, 100, reference="g", purpose="test")
    with override_settings(DCM_BILLING_APP="maana"):
        record_ai_call(user=user, provider="openai_api", model="gpt-4o-mini", input_tokens=10, output_tokens=5, purpose="chat", reference="a")
        debit_tokens(user, 7, purpose="chat", reference="debit-maana")
    with override_settings(DCM_BILLING_APP="cinevia"):
        record_ai_call(user=user, provider="openai_api", model="gpt-4o-mini", input_tokens=1000, output_tokens=1000, purpose="chat", reference="b")
        debit_tokens(user, 30, purpose="chat", reference="debit-cinevia")
    token = "report-secret"
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_REPORT_TOKEN_SHA256=[hashlib.sha256(token.encode()).hexdigest()]):
        request = RequestFactory().get("/cost-report/", HTTP_AUTHORIZATION=f"Bearer {token}")
        data = json.loads(cost_report(request).content)
    assert data["app"] == "maana" and data["calls"] == 1 and data["input_tokens"] == 10 and data["tokens_debited"] == 7


def test_ai_cost_record_keeps_provider_model_usage_cost_and_reference():
    user = _user("record@example.com")
    with override_settings(DCM_BILLING_APP="maana"):
        row = record_ai_call(user=user, provider="openai_api", model="gpt-4o-mini", input_tokens=2000, output_tokens=1000,
                             cache_tokens=300, purpose="chat", reference="turn-1")
    # gpt-4o-mini (legacy table): $0.15 / $0.60 per million -> 2000 * 0.15 + 1000 * 0.60 = 900 micro-USD, plus 300 cache tokens at the input rate = 45
    assert (row.app, row.provider, row.model, row.input_tokens, row.output_tokens, row.cache_tokens, row.purpose, row.reference) == \
        ("maana", "openai_api", "gpt-4o-mini", 2000, 1000, 300, "chat", "turn-1")
    assert row.cost_usd_micros == 945


def test_a_reused_debit_reference_with_another_amount_is_refused():
    user = _user("reuse@example.com")
    grant_tokens(user, 100, reference="g", purpose="test")
    debit_tokens(user, 10, purpose="chat", reference="same")
    assert debit_tokens(user, 10, purpose="chat", reference="same").amount == -10  # an exact retry is idempotent
    with pytest.raises(ValueError):
        debit_tokens(user, 50, purpose="chat", reference="same")
    assert TokenEntry.objects.filter(kind="debit").count() == 1


def test_unpaid_team_checkout_creates_no_package_and_the_async_payment_event_does():
    user = _user("team@example.com")
    import yaml, tempfile, pathlib
    path = pathlib.Path(tempfile.mkdtemp()) / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": [
        {"key": "team", "kind": "team_package", "name": "Team", "amount_cents": 1000, "term_months": 6, "seats": 3,
         "token_allowance": 50, "tax_code": "txcd_10103001"}], "token_rates": {"chat": 2}}), encoding="utf-8")
    session = {"id": "cs_1", "payment_intent": "pi_1", "created": int(timezone.now().timestamp()), "payment_status": "unpaid",
               "metadata": {"app": "maana", "plan_key": "team", "user_id": str(user.pk)}}
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        assert stripe_service.process_stripe_event({"type": "checkout.session.completed", "data": {"object": session}}) == "ignored"
        assert not TeamPackage.objects.exists()
        session["payment_status"] = "paid"
        assert stripe_service.process_stripe_event({"type": "checkout.session.async_payment_succeeded", "data": {"object": session}}) == "processed"
    assert TeamPackage.objects.filter(stripe_ref="pi_1", seats=3).exists()


def test_checkout_endpoint_sends_managed_payments_and_the_app_metadata_to_stripe(monkeypatch, tmp_path):
    import yaml
    user = _user("co@example.com")
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": [
        {"key": "monthly", "kind": "subscription", "name": "M", "amount_cents": 1500, "interval": "month", "tax_code": "txcd_10103001"}],
        "token_rates": {"chat": 2}}), encoding="utf-8")
    from django_core_micha.billing.models import StripePrice
    StripePrice.objects.create(app="maana", plan_key="monthly", version=1, lookup_key="maana:monthly:v1", fingerprint="x",
                               stripe_product_id="prod_1", stripe_price_id="price_1")
    sent = {}
    fake = SimpleNamespace(
        Customer=SimpleNamespace(create=lambda **kw: SimpleNamespace(id="cus_1")),
        checkout=SimpleNamespace(Session=SimpleNamespace(create=lambda **kw: sent.update(kw) or SimpleNamespace(url="https://stripe.test/c"))))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    request = RequestFactory().post("/checkout/", data=json.dumps({"plan_key": "monthly"}), content_type="application/json")
    request.user = user
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path, DCM_BILLING_SUCCESS_URL="https://x/ok", DCM_BILLING_CANCEL_URL="https://x/no"):
        response = checkout(request)
    assert response.status_code == 200 and json.loads(response.content)["url"] == "https://stripe.test/c"
    assert sent["managed_payments"] == {"enabled": True} and sent["metadata"]["app"] == "maana" and sent["customer"] == "cus_1"
    assert sent["subscription_data"]["metadata"]["app"] == "maana"


def test_a_purchase_needs_a_reference_and_an_invalid_month_is_a_controlled_400():
    from django_core_micha.billing.services import credit_purchase
    user = _user("purchase@example.com")
    with pytest.raises(ValueError):
        credit_purchase(user, 10, reference="")
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_REPORT_TOKEN_SHA256=[hashlib.sha256(b"t").hexdigest()]):
        for month in ("2026-13", "2026-00", "abcd-ef"):
            request = RequestFactory().get("/cost-report/", {"month": month}, HTTP_AUTHORIZATION="Bearer t")
            assert cost_report(request).status_code == 400


def test_cost_record_failure_does_not_poison_the_callers_transaction(monkeypatch):
    from django.db import transaction
    user = _user("savepoint@example.com")
    def broken(*args, **kwargs):
        raise RuntimeError("insert failed")
    monkeypatch.setattr(AICostRecord.objects, "create", broken)
    with transaction.atomic():
        assert record_ai_call(user=user, provider="openai_api", model="gpt-4o-mini", input_tokens=1, output_tokens=1, purpose="chat") is None
        TokenEntry.objects.count()  # the surrounding transaction is still usable


def test_renewal_in_a_new_period_grants_a_second_allowance(monkeypatch, tmp_path):
    import yaml
    user = _user("renew@example.com")
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": [
        {"key": "monthly", "kind": "subscription", "name": "M", "amount_cents": 1500, "interval": "month", "token_allowance": 100,
         "tax_code": "txcd_10103001"}], "token_rates": {"chat": 2}}), encoding="utf-8")
    now = int(timezone.now().timestamp())
    periods = [{"start": now, "end": now + 86400 * 30}, {"start": now + 86400 * 30, "end": now + 86400 * 60}]
    state = {"i": 0}
    def retrieve(sid):
        p = periods[state["i"]]
        return {"id": "sub_renew", "status": "active", "current_period_start": p["start"], "current_period_end": p["end"],
                "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}
    monkeypatch.setattr(stripe_service, "_stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(retrieve=retrieve)))
    paid = {"type": "invoice.paid", "data": {"object": {"subscription": "sub_renew", "metadata": {"app": "maana"}}}}
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        stripe_service.process_stripe_event(paid)
        stripe_service.process_stripe_event(paid)  # the same period again: no second grant
        assert TokenEntry.objects.filter(kind="grant", reference__startswith="stripe:sub_renew:").count() == 1
        state["i"] = 1
        stripe_service.process_stripe_event(paid)
    assert TokenEntry.objects.filter(kind="grant", reference__startswith="stripe:sub_renew:").count() == 2


_VALID_SUB = {"key": "monthly", "kind": "subscription", "name": "M", "amount_cents": 1500, "interval": "month",
              "token_allowance": 10, "tax_code": "txcd_10103001"}


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(app="other"),
    lambda c: c.update(currency="eur"),
    lambda c: c["plans"][0].update(unknown="x"),
    lambda c: c["plans"][0].update(amount_cents=0),
    lambda c: c["plans"][0].update(interval="week"),
    lambda c: c["plans"][0].pop("tax_code"),
    lambda c: c["plans"][0].update(kind="team_package"),
    lambda c: c["plans"][0].update(seats=3),
    lambda c: c["plans"].append(dict(c["plans"][0])),
    lambda c: c["token_rates"].update(chat=1.5),
    lambda c: c["token_rates"].update(chat=0),
])
def test_billing_yaml_rejects_inconsistent_files(tmp_path, mutate):
    import copy, yaml
    from django_core_micha.billing.config import load_billing_config
    config = {"app": "maana", "currency": "usd", "plans": [dict(_VALID_SUB)], "token_rates": {"chat": 2}}
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump(copy.deepcopy(config)), encoding="utf-8")
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        assert load_billing_config().plans[0].key == "monthly"  # the unmodified file is valid
        broken = copy.deepcopy(config)
        mutate(broken)
        path.write_text(yaml.safe_dump(broken), encoding="utf-8")
        with pytest.raises(ValueError):
            load_billing_config()


def test_a_debit_needs_a_reference():
    user = _user("noref@example.com")
    grant_tokens(user, 100, reference="g", purpose="test")
    with pytest.raises(ValueError):
        debit_tokens(user, 5, purpose="chat", reference="")


def test_an_invoice_marked_for_this_app_cannot_update_a_subscription_of_another_app(monkeypatch, tmp_path):
    import yaml
    user = _user("foreignsub@example.com")
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": [
        {"key": "monthly", "kind": "subscription", "name": "M", "amount_cents": 1500, "interval": "month", "token_allowance": 100,
         "tax_code": "txcd_10103001"}], "token_rates": {"chat": 2}}), encoding="utf-8")
    now = int(timezone.now().timestamp())
    foreign = {"id": "sub_foreign", "status": "active", "current_period_start": now, "current_period_end": now + 86400,
               "metadata": {"app": "cinevia", "plan_key": "monthly", "user_id": str(user.pk)}}
    monkeypatch.setattr(stripe_service, "_stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid: foreign)))
    invoice = {"type": "invoice.paid", "data": {"object": {"subscription": "sub_foreign", "metadata": {"app": "maana"}}}}
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        stripe_service.process_stripe_event(invoice)
    from django_core_micha.billing.models import Subscription
    assert not Subscription.objects.filter(stripe_subscription_id="sub_foreign").exists()
    assert not TokenEntry.objects.filter(reference__startswith="stripe:sub_foreign:").exists()


def test_the_entitlements_bought_at_checkout_survive_a_later_edit_of_billing_yaml(tmp_path):
    import yaml
    user = _user("snapshot@example.com")
    path = tmp_path / "billing.yaml"
    def write(seats):
        path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": [
            {"key": "team", "kind": "team_package", "name": "Team", "amount_cents": 1000, "term_months": 6, "seats": seats,
             "token_allowance": 50, "tax_code": "txcd_10103001"}], "token_rates": {"chat": 2}}), encoding="utf-8")
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path, DCM_BILLING_SUCCESS_URL="https://x/ok", DCM_BILLING_CANCEL_URL="https://x/no"):
        write(5)
        from django_core_micha.billing.config import load_billing_config
        plan = load_billing_config().plans[0]
        price = SimpleNamespace(stripe_price_id="price_1")
        metadata = stripe_service.checkout_session_params(plan=plan, price=price, user=user, customer_id="cus_1")["metadata"]
        assert metadata["seats"] == "5" and metadata["term_months"] == "6" and metadata["token_allowance"] == "50"
        write(2)  # the operator edits the plan before the payment event is processed
        session = {"id": "cs_snap", "payment_intent": "pi_snap", "payment_status": "paid", "created": int(timezone.now().timestamp()), "metadata": metadata}
        assert stripe_service.process_stripe_event({"type": "checkout.session.completed", "data": {"object": session}}) == "processed"
    assert TeamPackage.objects.get(stripe_ref="pi_snap").seats == 5


def test_a_checkout_session_without_payment_status_grants_nothing_and_a_trial_grants_no_tokens(tmp_path):
    import yaml
    user = _user("failclosed@example.com")
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": [
        {"key": "tokens", "kind": "token_package", "name": "T", "amount_cents": 1000, "tokens": 25, "tax_code": "txcd_10103001"},
        {"key": "monthly", "kind": "subscription", "name": "M", "amount_cents": 1500, "interval": "month", "token_allowance": 100,
         "tax_code": "txcd_10103001"}], "token_rates": {"chat": 2}}), encoding="utf-8")
    now = int(timezone.now().timestamp())
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        session = {"id": "cs_nostatus", "metadata": {"app": "maana", "plan_key": "tokens", "user_id": str(user.pk)}}
        stripe_service.process_stripe_event({"type": "checkout.session.completed", "data": {"object": session}})
        trial = {"id": "sub_trial", "status": "trialing", "current_period_start": now, "current_period_end": now + 86400,
                 "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}
        stripe_service.process_stripe_event({"type": "customer.subscription.created", "data": {"object": trial}})
    assert not TokenEntry.objects.filter(kind="purchase").exists()
    assert not TokenEntry.objects.filter(reference__startswith="stripe:sub_trial:").exists()


@pytest.mark.parametrize("view_name,path", [("checkout", "/checkout/"), ("portal", "/portal/"), ("team_seats", "/team/seats/")])
def test_session_authenticated_billing_mutations_enforce_csrf(view_name, path):
    from django_core_micha.billing import views
    user = _user(f"csrf-{view_name}@example.com")
    request = RequestFactory().post(path, data="{}", content_type="application/json")
    request.user = user
    request._dont_enforce_csrf_checks = False  # a real browser request without a CSRF token
    assert getattr(views, view_name)(request).status_code == 403
