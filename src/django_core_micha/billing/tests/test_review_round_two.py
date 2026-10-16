from datetime import timedelta
import pytest
import yaml
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from django_core_micha.ai_prices import price_usage
from django_core_micha.billing.models import Purchase, TokenEntry, TokenAccount
from django_core_micha.billing.services import credit_purchase, grant_tokens
from django_core_micha.billing.stripe_service import process_stripe_event
from django_core_micha.billing.withdrawal import refund_amount

pytestmark = pytest.mark.django_db


def user(i): return get_user_model().objects.create_user(username=f"r2-{i}", email=f"r2-{i}@example.com", password="x")


def purchase(u, **kw):
    now = timezone.now()
    vals = dict(user=u, kind="subscription", plan_key="p", stripe_object_id=f"o-{u.pk}", amount_paid_cents=1000, currency="eur",
                billing_country="DE", purchased_at=now, term_start=now, term_end=now + timedelta(days=10), grant_references=[], tokens_granted=0)
    vals.update(kw)
    return Purchase.objects.create(**vals)


def test_refund_uses_larger_share_time_over_token_and_token_over_time():
    u = user(1); grant_tokens(u, 100, reference="g1", purpose="x")
    p = purchase(u, grant_references=["g1"], tokens_granted=100)
    entry = TokenEntry.objects.get(reference="g1"); entry.remaining = 90; entry.save(update_fields=("remaining",))
    calc = refund_amount(p, p.term_start + timedelta(days=5))  # time 500 > token 100
    assert (calc.time_share_cents, calc.token_share_cents, calc.used_cents, calc.refund_cents) == (500, 100, 500, 500)
    entry.remaining = 20; entry.save(update_fields=("remaining",))
    calc = refund_amount(p, p.term_start + timedelta(days=1))  # time 100 < token 800
    assert (calc.time_share_cents, calc.token_share_cents, calc.refund_cents) == (100, 800, 200)


def test_token_package_refund_is_proportional():
    u = user(2); credit_purchase(u, 50, reference="stripe:checkout:cs")
    p = purchase(u, kind="token_package", grant_references=["stripe:checkout:cs"], tokens_granted=50, term_start=None, term_end=None)
    entry = TokenEntry.objects.get(reference="stripe:checkout:cs"); entry.remaining = 40; entry.save(update_fields=("remaining",))
    calc = refund_amount(p, timezone.now())
    assert calc.token_share_cents == 200 and calc.refund_cents == 800


def test_token_share_only_counts_the_purchase_owners_account():
    owner, other = user(3), user(4)
    grant_tokens(owner, 10, reference="same-ref", purpose="x"); grant_tokens(other, 10, reference="same-ref", purpose="x")
    TokenEntry.objects.filter(account__user=other).update(remaining=0)
    p = purchase(owner, grant_references=["same-ref"], tokens_granted=10)
    assert refund_amount(p, p.term_start).token_share_cents == 0


def _cfg(tmp_path):
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "app", "currency": "usd", "plans": [{"key": "tokens_10", "kind": "token_package", "name": "t", "amount_cents": 500, "tokens": 10, "tax_code": "txcd_10103001"}], "token_rates": {}}), encoding="utf-8")
    return str(path)


def _event(u, **obj):
    base = {"id": "cs_z", "payment_intent": "pi_z", "payment_status": "paid", "currency": "usd",
            "metadata": {"app": "app", "plan_key": "tokens_10", "user_id": str(u.pk), "tokens": "10"}, "customer_details": {"address": {"country": "DE"}}}
    base.update(obj)
    return {"id": "evt-z", "type": "checkout.session.completed", "created": int(timezone.now().timestamp()), "data": {"object": base}}


def test_zero_amount_checkout_records_zero_and_missing_amount_falls_back_to_plan(tmp_path):
    u = user(5)
    with override_settings(DCM_BILLING_APP="app", DCM_BILLING_FILE=_cfg(tmp_path)):
        process_stripe_event(_event(u, amount_total=0))
        assert Purchase.objects.get(stripe_object_id="pi_z").amount_paid_cents == 0
        process_stripe_event(_event(u, id="cs_y", payment_intent="pi_y"))
    assert Purchase.objects.get(stripe_object_id="pi_y").amount_paid_cents == 500


def test_charge_refunded_without_app_metadata_is_recorded_noop():
    with override_settings(DCM_BILLING_APP="app"):
        assert process_stripe_event({"type": "charge.refunded", "data": {"object": {"metadata": {}}}}) == "processed"


def test_invoice_paid_after_withdrawal_does_not_reopen_access(monkeypatch):
    from types import SimpleNamespace
    from django_core_micha.billing.models import Subscription
    u = user(6); p = purchase(u, stripe_object_id="sub-w"); p.status = "withdrawn"; p.save(update_fields=("status",))
    now = int(timezone.now().timestamp())
    remote = {"id": "sub-w", "status": "active", "metadata": {"app": "app", "plan_key": "monthly", "user_id": str(u.pk)}, "current_period_start": now, "current_period_end": now + 3600}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid: remote)))
    with override_settings(DCM_BILLING_APP="app"):
        process_stripe_event({"type": "invoice.paid", "data": {"object": {"subscription": "sub-w", "metadata": {"app": "app"}}}})
    assert not Subscription.objects.filter(stripe_subscription_id="sub-w").exists()


def test_incomplete_price_row_never_prices_at_zero():
    with override_settings(AI_MODEL_PRICES_USD_PER_MILLION={"m": {"cache_read": 1}}):
        assert price_usage("m", input_tokens=1000, output_tokens=1000) > 0


def test_fully_used_purchase_withdraws_without_a_refund_call_and_email_has_the_details(monkeypatch, django_capture_on_commit_callbacks):
    from django.core import mail
    from django_core_micha.billing.withdrawal import withdraw
    from types import SimpleNamespace
    u = user(7); u.first_name = "Anna"; u.save()
    p = purchase(u, kind="token_package", stripe_object_id="pi-used", plan_key="tokens_10", grant_references=["used-ref"], tokens_granted=10, term_start=None, term_end=None)
    credit_purchase(u, 10, reference="used-ref")
    TokenEntry.objects.filter(reference="used-ref").update(remaining=0)
    calls = []
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(Refund=SimpleNamespace(create=lambda **kw: calls.append(kw))))
    with override_settings(DCM_BILLING_APP="app"), django_capture_on_commit_callbacks(execute=True):
        calc, already = withdraw(p)
    p.refresh_from_db()
    assert calc.refund_cents == 0 and calls == [] and p.status == "withdrawn" and p.stripe_refund_id == ""
    assert len(mail.outbox) == 1 and "tokens_10" in mail.outbox[0].body and "1000" in mail.outbox[0].body and "14" in mail.outbox[0].body


def test_unpaid_subscription_checkout_creates_no_purchase_until_payment(monkeypatch, tmp_path):
    from types import SimpleNamespace
    path = tmp_path / "billing.yaml"
    path.write_text(yaml.safe_dump({"app": "app", "currency": "usd", "plans": [{"key": "monthly", "kind": "subscription", "name": "m", "amount_cents": 900, "interval": "month", "token_allowance": 5, "tax_code": "txcd_10103001"}], "token_rates": {}}), encoding="utf-8")
    u = user(8); now = int(timezone.now().timestamp())
    remote = {"id": "sub-u", "status": "incomplete", "metadata": {"app": "app", "plan_key": "monthly", "user_id": str(u.pk)}, "current_period_start": now, "current_period_end": now + 3600, "latest_invoice": {}}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid: remote)))
    event = {"id": "e1", "type": "checkout.session.completed", "created": now, "data": {"object": {"id": "cs_u", "subscription": "sub-u", "payment_status": "unpaid", "amount_total": 900, "metadata": {"app": "app", "plan_key": "monthly", "user_id": str(u.pk)}}}}
    with override_settings(DCM_BILLING_APP="app", DCM_BILLING_FILE=str(path)):
        process_stripe_event(event)
        assert not Purchase.objects.filter(stripe_object_id="sub-u").exists()
        event["data"]["object"]["payment_status"] = "paid"; event["type"] = "checkout.session.async_payment_succeeded"
        process_stripe_event(event)
    assert Purchase.objects.filter(stripe_object_id="sub-u").count() == 1


def test_withdrawal_without_payment_reference_is_refused_untouched():
    from django_core_micha.billing.withdrawal import withdraw
    u = user(9); p = purchase(u, stripe_object_id="sub-nopi", stripe_payment_intent_id="")
    with pytest.raises(ValueError, match="payment_reference_missing"):
        withdraw(p)
    p.refresh_from_db(); assert p.status == "active"
