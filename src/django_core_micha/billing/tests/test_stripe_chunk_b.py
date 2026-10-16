import hashlib
import hmac
import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing import stripe_service
from django_core_micha.billing.models import BillingCustomer, FreeAccess, StripeEvent, StripePrice, Subscription, TeamPackage, TokenEntry
from django_core_micha.billing.services import access_state
from django_core_micha.billing.views import webhook

pytestmark = pytest.mark.django_db


def _user(email="stripe@example.com"):
    return get_user_model().objects.create_user(username=email, email=email)


def _config(tmp_path, plans=None):
    import yaml
    plans = plans or [{"key": "monthly", "kind": "subscription", "name": "Monthly", "amount_cents": 1500,
        "interval": "month", "token_allowance": 100, "tax_code": "txcd_10103001"}]
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "maana", "currency": "usd", "plans": plans,
        "token_rates": {"chat": 2}}), encoding="utf-8")
    return path


class _Page:
    def __init__(self, rows): self.data = rows
    def auto_paging_iter(self): return iter(self.data)


class _Stripe:
    def __init__(self):
        self.products = []
        self.prices = []
        self.created_prices = []
        self.created_products = []
        self.modified_products = []
        self.Product = SimpleNamespace(list=lambda **kw: _Page(self.products), create=self.create_product,
            modify=lambda pid, **kw: self.modified_products.append((pid, kw)), retrieve=self.retrieve_product)
        self.Price = SimpleNamespace(list=self.list_prices, create=self.create_price)
    def retrieve_product(self, pid):
        return next(p for p in self.products if p.id == pid)
    def create_product(self, **kwargs):
        row = SimpleNamespace(id=f"prod_{len(self.created_products)+1}", metadata=kwargs["metadata"], tax_code=kwargs.get("tax_code"))
        self.created_products.append((kwargs, row)); self.products.append(row); return row
    def list_prices(self, **kwargs): return _Page([p for p in self.prices if p.lookup_key in kwargs["lookup_keys"]])
    def create_price(self, **kwargs):
        row = SimpleNamespace(id=f"price_{len(self.created_prices)+1}", lookup_key=kwargs["lookup_key"])
        self.created_prices.append((kwargs, row)); self.prices.append(row); return row


def test_sync_is_idempotent_and_changed_price_creates_new_version(tmp_path, monkeypatch):
    path = _config(tmp_path)
    fake = _Stripe()
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        assert stripe_service.sync_plans() == [("monthly", "v1")]
        assert stripe_service.sync_plans() == [("monthly", "unchanged")]
        assert len(fake.created_prices) == 1
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["plans"][0]["amount_cents"] = 1800
        data["plans"][0]["tax_code"] = "txcd_20090000"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        assert stripe_service.sync_plans() == [("monthly", "v2")]
    assert [p.lookup_key for p in StripePrice.objects.order_by("version")] == ["maana:monthly:v1", "maana:monthly:v2"]
    assert len(fake.created_prices) == 2
    assert len(fake.created_products) == 2  # the changed tax code opened a new Product
    assert fake.created_prices[0][0]["unit_amount"] == 1500
    assert fake.created_products[1][0]["tax_code"] == "txcd_20090000" and fake.modified_products == []  # v1 subscribers keep their tax treatment


def test_sync_dry_run_makes_no_stripe_calls(tmp_path, monkeypatch):
    path = _config(tmp_path)
    def fail(): raise AssertionError("dry run called Stripe")
    monkeypatch.setattr(stripe_service, "_stripe", fail)
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        assert stripe_service.sync_plans(dry_run=True) == [("monthly", "would_sync")]


def test_checkout_session_parameters_match_stripe_16_checkout_fields(tmp_path):
    from django_core_micha.billing.config import load_billing_config
    path = _config(tmp_path)
    user = _user()
    price = StripePrice(app="maana", plan_key="monthly", version=1, lookup_key="maana:monthly:v1",
        fingerprint="x", stripe_product_id="prod_1", stripe_price_id="price_1")
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path,
            DCM_BILLING_SUCCESS_URL="https://app.example/success", DCM_BILLING_CANCEL_URL="https://app.example/cancel"):
        plan = load_billing_config().plans[0]
        params = stripe_service.checkout_session_params(plan=plan, price=price, user=user, customer_id="cus_1")
    assert params["mode"] == "subscription"
    assert params["metadata"] == {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk), "token_allowance": "100"}
    assert params["subscription_data"]["metadata"] == {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}
    assert params["automatic_tax"] == {"enabled": True}
    assert params["managed_payments"] == {"enabled": True}
    assert "tax_id_collection" not in params
    from stripe.params.checkout._session_create_params import SessionCreateParams
    assert set(params) <= set(SessionCreateParams.__annotations__)


def test_one_time_checkout_sets_payment_intent_metadata(tmp_path):
    import yaml
    from django_core_micha.billing.config import load_billing_config
    plans = [{"key":"tokens","kind":"token_package","name":"Tokens","amount_cents":1000,"tokens":20,"tax_code":"txcd_1"}]
    path = _config(tmp_path, plans)
    user = _user("payment-intent@example.com")
    price = StripePrice(app="maana", plan_key="tokens", version=1, lookup_key="maana:tokens:v1", fingerprint="x", stripe_product_id="prod_1", stripe_price_id="price_1")
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        params = stripe_service.checkout_session_params(plan=load_billing_config().plans[0], price=price, user=user, customer_id="cus_1")
    from stripe.params.checkout._session_create_params import SessionCreateParams
    assert set(params) <= set(SessionCreateParams.__annotations__)
    assert params["payment_intent_data"]["metadata"] == params["metadata"]


def _signed_request(event, secret="whsec_test"):
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    req = RequestFactory().post("/webhook/", data=payload, content_type="application/json",
        HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}")
    return req


def test_webhook_rejects_bad_signature_and_replay_is_idempotent(tmp_path):
    user = _user("hook@example.com")
    path = _config(tmp_path)
    now = int(timezone.now().timestamp())
    event = {"id": "evt_replay", "type": "customer.subscription.created", "data": {"object": {
        "id": "sub_replay", "status": "active", "current_period_start": now,
        "current_period_end": now + 86400, "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}}}
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path, STRIPE_SECRET_KEY="sk_test_x",
            STRIPE_WEBHOOK_SECRET="whsec_test"):
        invalid = RequestFactory().post("/webhook/", data=json.dumps(event), content_type="application/json", HTTP_STRIPE_SIGNATURE="bad")
        response = webhook(invalid)
        assert response.status_code == 400 and response.content == b""
        assert webhook(_signed_request(event)).status_code == 200
        assert webhook(_signed_request(event)).status_code == 200
    assert StripeEvent.objects.filter(event_id="evt_replay", status="processed").count() == 1
    assert Subscription.objects.filter(stripe_subscription_id="sub_replay").count() == 1
    assert TokenEntry.objects.filter(kind="grant", reference__startswith="stripe:sub_replay:").count() == 1


def test_foreign_event_ignored_and_subscription_lifecycle_sets_access(tmp_path, monkeypatch):
    path = _config(tmp_path)
    user = _user("lifecycle@example.com")
    now = int(timezone.now().timestamp())
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        foreign = {"type": "customer.subscription.created", "data": {"object": {"id": "sub_foreign", "metadata": {"app": "cinevia"}}}}
        assert stripe_service.process_stripe_event(foreign) == "ignored"
        for event_type in ("customer.subscription.created", "customer.subscription.updated"):
            event = {"type": event_type, "data": {"object": {"id": "sub_life", "status": "active",
                "current_period_start": now, "current_period_end": now + 86400,
                "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}}}
            assert stripe_service.process_stripe_event(event) == "processed"
            assert access_state(user).status == "active"
        failed = Subscription.objects.get(stripe_subscription_id="sub_life")
        failed.status = "past_due"; failed.save(update_fields=("status",))
        assert access_state(user).status == "active"
        ended = {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_life", "status": "canceled",
            "canceled_at": now, "current_period_start": now-86400, "current_period_end": now-1,
            "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}}}
        stripe_service.process_stripe_event(ended)
        assert access_state(user).status == "read_only"
        assert Subscription.objects.get(stripe_subscription_id="sub_life").status == "canceled"


def test_invoice_payment_failure_and_paid_renewal(tmp_path, monkeypatch):
    path = _config(tmp_path)
    user = _user("invoice@example.com")
    now = int(timezone.now().timestamp())
    sub_data = {"id": "sub_invoice", "status": "active", "current_period_start": now,
        "current_period_end": now + 86400, "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}
    fake = SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid: sub_data))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        stripe_service.process_stripe_event({"type":"customer.subscription.created","data":{"object":sub_data}})
        failed = {"type":"invoice.payment_failed","data":{"object":{"subscription":"sub_invoice",
            "metadata":{"app":"maana"}}}}
        stripe_service.process_stripe_event(failed)
        assert Subscription.objects.get(stripe_subscription_id="sub_invoice").status == "past_due"
        paid = {"type":"invoice.paid","data":{"object":{"subscription":"sub_invoice",
            "metadata":{"app":"maana"}}}}
        stripe_service.process_stripe_event(paid)
    assert Subscription.objects.get(stripe_subscription_id="sub_invoice").status == "active"
    assert TokenEntry.objects.filter(kind="grant", reference__startswith="stripe:sub_invoice:").count() == 1


def test_completed_checkout_grants_team_and_token_purchases(tmp_path):
    import yaml
    plans = [
        {"key":"team6","kind":"team_package","name":"Team","amount_cents":60000,"term_months":6,"seats":3,"token_allowance":50,"tax_code":"txcd_1"},
        {"key":"tokens","kind":"token_package","name":"Tokens","amount_cents":1000,"tokens":25,"tax_code":"txcd_1"},
    ]
    path = _config(tmp_path, plans)
    user = _user("buy@example.com")
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        team = {"type":"checkout.session.completed","data":{"object":{"id":"cs_team","payment_intent":"pi_team","payment_status":"paid","created":int(timezone.now().timestamp()),"metadata":{"app":"maana","plan_key":"team6","user_id":str(user.pk)}}}}
        tokens = {"type":"checkout.session.completed","data":{"object":{"id":"cs_tokens","payment_status":"paid","metadata":{"app":"maana","plan_key":"tokens","user_id":str(user.pk)}}}}
        stripe_service.process_stripe_event(team); stripe_service.process_stripe_event(team)
        stripe_service.process_stripe_event(tokens); stripe_service.process_stripe_event(tokens)
    assert TeamPackage.objects.filter(stripe_ref="pi_team", seats=3).count() == 1
    assert TokenEntry.objects.filter(kind="grant", purpose="plan:team6").count() == 1
    assert TokenEntry.objects.filter(kind="purchase", reference="stripe:checkout:cs_tokens").count() == 1


def test_portal_requires_existing_customer_and_uses_return_url(monkeypatch):
    from django.test import RequestFactory
    from django_core_micha.billing.views import portal
    user = _user("portal@example.com")
    request = RequestFactory().post("/portal/"); request.user = user
    assert portal(request).status_code == 404
    BillingCustomer.objects.create(user=user, stripe_customer_id="cus_portal")
    calls = []
    fake = SimpleNamespace(billing_portal=SimpleNamespace(Session=SimpleNamespace(create=lambda **kw: (calls.append(kw) or SimpleNamespace(url="https://stripe.example/portal")))))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    with override_settings(DCM_BILLING_PORTAL_RETURN_URL="https://app.example/billing"):
        response = portal(request)
    assert response.status_code == 200
    assert calls == [{"customer":"cus_portal", "return_url":"https://app.example/billing"}]


def test_reconcile_repairs_missed_subscription_event(monkeypatch, tmp_path):
    from django.core.management import call_command
    user = _user("reconcile@example.com")
    now = int(timezone.now().timestamp())
    remote = {"id":"sub_reconcile", "status":"active", "current_period_start":now,
        "current_period_end":now+86400, "metadata":{"app":"maana","plan_key":"monthly","user_id":str(user.pk)}}
    fake = SimpleNamespace(
        Subscription=SimpleNamespace(list=lambda **kw: _Page([remote])),
        Event=SimpleNamespace(list=lambda **kw: _Page([])))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    path = _config(tmp_path)
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        call_command("billing_reconcile")
    assert Subscription.objects.filter(stripe_subscription_id="sub_reconcile", status="active").exists()
    assert TokenEntry.objects.filter(kind="grant", reference__startswith="stripe:sub_reconcile:").count() == 1


def test_revoked_free_access_ends_access_at_revocation_and_stays_read_only():
    user = _user("revoked@example.com")
    revoked = timezone.now() - timedelta(days=100)
    FreeAccess.objects.create(user=user, granted_by_label="ops", reason="temporary", granted_at=revoked-timedelta(days=10), revoked_at=revoked)
    state = access_state(user)
    assert state.status == "read_only"
    assert state.ends_at == revoked
    assert not hasattr(state, "delete_after")


def test_period_on_items_and_invoice_parent_subscription_are_understood(tmp_path, monkeypatch):
    """Newer Stripe API versions move the period to the subscription item and the invoice's subscription to `parent`."""
    path = _config(tmp_path)
    user = _user("newapi@example.com")
    now = int(timezone.now().timestamp())
    sub_data = {"id": "sub_new", "status": "active", "items": {"data": [{"current_period_start": now, "current_period_end": now + 86400}]},
        "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}
    fake = SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid: sub_data))
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        assert stripe_service.process_stripe_event({"type": "customer.subscription.created", "data": {"object": sub_data}}) == "processed"
        assert access_state(user).status == "active"
        invoice = {"type": "invoice.paid", "data": {"object": {"metadata": {"app": "maana"},
            "parent": {"subscription_details": {"subscription": "sub_new"}}}}}
        assert stripe_service.process_stripe_event(invoice) == "processed"
        failed = {"type": "invoice.payment_failed", "data": {"object": {"metadata": {"app": "maana"},
            "parent": {"subscription_details": {"subscription": "sub_new"}}}}}
        stripe_service.process_stripe_event(failed)
    assert Subscription.objects.get(stripe_subscription_id="sub_new").status == "past_due"


def test_a_past_due_period_grants_no_tokens(tmp_path):
    path = _config(tmp_path)
    user = _user("pastdue@example.com")
    now = int(timezone.now().timestamp())
    with override_settings(DCM_BILLING_APP="maana", DCM_BILLING_FILE=path):
        event = {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_pd", "status": "past_due",
            "current_period_start": now, "current_period_end": now + 86400,
            "metadata": {"app": "maana", "plan_key": "monthly", "user_id": str(user.pk)}}}}
        stripe_service.process_stripe_event(event)
    assert not TokenEntry.objects.filter(reference__startswith="stripe:sub_pd:").exists()
    assert access_state(user).status == "active"  # access continues during Stripe's retries
