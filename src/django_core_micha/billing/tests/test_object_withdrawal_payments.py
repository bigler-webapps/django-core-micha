from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing.contracts import apply_withdrawal, resume_withdrawals
from django_core_micha.billing.declarations import apply_withdrawal_declaration
from django_core_micha.billing.models import Contract, ContractPayment, Declaration, ObjectBillingSubject, ObjectSubscription, Purchase, TokenAccount, TokenEntry
from django_core_micha.billing.views import contract_withdraw, withdrawal_preview

pytestmark = pytest.mark.django_db


def setup_contract(policy="full"):
    user = get_user_model().objects.create_user(username="former-payer")
    now = timezone.now()
    purchase = Purchase.objects.create(user=user, kind="object_subscription", plan_key="film", stripe_object_id="sub-object", amount_paid_cents=1000, currency="chf", billing_country="DE", purchased_at=now, term_start=now, term_end=now + timedelta(days=30))
    contract = Contract.objects.create(user=user, app="cinema", kind="object_subscription", plan_key="film", stripe_object_id="sub-object", purchase=purchase, withdrawal_policy=policy, consumer_status="consumer", billing_country="DE", started_at=now)
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    sub = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="film", stripe_subscription_id="sub-object", status="active")
    account = TokenAccount.objects.create(user=user)
    entry = TokenEntry.objects.create(account=account, kind="purchase", amount=50, remaining=50, reference="unrelated", app="cinema")
    return user, contract, sub, entry


def provider(now, refund=None):
    stamp = int(now.timestamp())
    def invoice(name, amount, start, end, reason):
        return {"id": name, "subscription": "sub-object", "status": "paid", "created": stamp - 1, "period_start": start, "period_end": end, "amount_paid": amount, "currency": "chf", "payment_intent": "pi-" + name if amount else "", "billing_reason": reason}
    invoices = [invoice("initial", 1000, stamp - 40*86400, stamp - 30*86400, "subscription_create"),
                invoice("renewal", 900, stamp - 5*86400 + 1, stamp + 5*86400 + 1, "subscription_cycle"),
                invoice("zero", 0, stamp - 1, stamp + 86400, "subscription_cycle")]
    return SimpleNamespace(Invoice=SimpleNamespace(list=Mock(return_value=SimpleNamespace(data=invoices))), InvoicePayment=SimpleNamespace(list=Mock(return_value=SimpleNamespace(data=[]))),
        Subscription=SimpleNamespace(cancel=Mock()), PaymentIntent=SimpleNamespace(retrieve=Mock(return_value={})),
        Refund=SimpleNamespace(create=refund or Mock(side_effect=lambda **kw: {"id": "re-" + kw["payment_intent"], "status": "succeeded"})))


@pytest.mark.parametrize("policy,expected", [("full", [1000, 900]), ("prorated", [450])])
def test_recovery_refunds_only_contract_payments_and_preserves_tokens(monkeypatch, django_capture_on_commit_callbacks, policy, expected):
    user, contract, sub, entry = setup_contract(policy)
    declaration = Declaration.objects.create(app="cinema", user=user, contract=contract, kind="withdrawal", channel="account")
    stripe = provider(declaration.received_at)
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    with django_capture_on_commit_callbacks(execute=True):
        apply_withdrawal_declaration(declaration)
    declaration.refresh_from_db(); sub.refresh_from_db(); entry.refresh_from_db()
    assert declaration.status == "effective" and sub.status == "canceled" and entry.remaining == 50
    assert sorted(p.amount_paid_cents for p in contract.payments.all()) == [0, 900, 1000]
    calls = stripe.Refund.create.call_args_list
    assert [call.kwargs["amount"] for call in calls] == expected
    assert len({call.kwargs["idempotency_key"] for call in calls}) == len(calls)
    assert all(call.kwargs["payment_intent"] in ("pi-initial", "pi-renewal") for call in calls)


def test_provider_failure_and_retry_reuses_payment_key(monkeypatch, django_capture_on_commit_callbacks):
    user, contract, sub, entry = setup_contract()
    declaration = Declaration.objects.create(app="cinema", user=user, contract=contract, kind="withdrawal", channel="account")
    calls = []
    def refund(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError()
        return {"id": "re", "status": "succeeded"}
    stripe = provider(declaration.received_at, Mock(side_effect=refund))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    with override_settings(DCM_BILLING_APP="cinema"):
        with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
        declaration.refresh_from_db()
        assert declaration.status == "accepted"
        resume_withdrawals()
    declaration.refresh_from_db(); entry.refresh_from_db()
    assert declaration.status == "effective" and entry.remaining == 50
    assert calls[0]["idempotency_key"] == calls[-1]["idempotency_key"]
    assert calls[0]["payment_intent"] == calls[-1]["payment_intent"]


def test_recovery_error_leaves_withdrawal_retryable(monkeypatch, django_capture_on_commit_callbacks):
    user, contract, sub, entry = setup_contract()
    declaration = Declaration.objects.create(app="cinema", user=user, contract=contract, kind="withdrawal", channel="account")
    stripe = provider(declaration.received_at)
    stripe.Invoice.list.side_effect = RuntimeError()
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
    declaration.refresh_from_db(); contract.refresh_from_db(); sub.refresh_from_db()
    assert declaration.status == "accepted" and contract.needs_review and sub.status == "active"
    stripe.Refund.create.assert_not_called()


def test_former_payer_can_withdraw_but_other_user_cannot(monkeypatch, django_capture_on_commit_callbacks):
    user, contract, _, _ = setup_contract()
    other = get_user_model().objects.create_user(username="other-admin")
    stripe = provider(timezone.now())
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    monkeypatch.setattr("django_core_micha.billing.views._contract_config_enabled", lambda: True)
    factory = RequestFactory()
    request = factory.post("/", data='{"confirm": true}', content_type="application/json"); request.user = other
    with override_settings(DCM_BILLING_APP="cinema"):
        with pytest.raises(Http404): contract_withdraw(request, contract.reference)
        request.user = user
        with django_capture_on_commit_callbacks(execute=True): response = contract_withdraw(request, contract.reference)
    assert response.status_code == 201


def test_unrelated_token_withdrawal_leaves_object_contract(monkeypatch, django_capture_on_commit_callbacks):
    user, object_contract, sub, entry = setup_contract()
    now = timezone.now()
    purchase = Purchase.objects.create(user=user, kind="token_package", plan_key="tokens", stripe_object_id="token-purchase", amount_paid_cents=500, currency="chf", purchased_at=now, grant_references=["token-grant"])
    token_contract = Contract.objects.create(user=user, app="cinema", kind="token_package", plan_key="tokens", stripe_object_id="token-purchase", purchase=purchase, withdrawal_policy="full", consumer_status="consumer", billing_country="DE", started_at=now)
    grant = TokenEntry.objects.create(account=entry.account, kind="purchase", amount=40, remaining=40, reference="token-grant", app="cinema")
    allowance = TokenEntry.objects.create(account=entry.account, kind="grant", amount=30, remaining=30, reference="stripe:personal-sub:renewal", app="cinema")
    ContractPayment.objects.create(contract=token_contract, stripe_payment_intent_id="pi-token", amount_paid_cents=500, currency="chf")
    stripe = provider(now)
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    declaration = Declaration.objects.create(app="cinema", user=user, contract=token_contract, kind="withdrawal", channel="account")
    with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
    object_contract.refresh_from_db(); sub.refresh_from_db(); grant.refresh_from_db(); allowance.refresh_from_db(); entry.refresh_from_db(); declaration.refresh_from_db()
    assert object_contract.status == "active" and sub.status == "active" and sub.blocks_purchase
    assert declaration.status == "effective" and grant.remaining == 0
    assert allowance.remaining == 30 and entry.remaining == 50
    assert stripe.Refund.create.call_args.kwargs["payment_intent"] == "pi-token"
    assert stripe.Refund.create.call_args.kwargs["amount"] == 500


@pytest.mark.parametrize("policy,expected", [("full", 1650), ("prorated", 300)])
def test_preview_matches_execution_with_renewal_and_partial_refunds(monkeypatch, django_capture_on_commit_callbacks, policy, expected):
    user, contract, sub, entry = setup_contract(policy)
    now = timezone.now().replace(microsecond=0)
    monkeypatch.setattr("django_core_micha.billing.views.timezone.now", lambda: now)
    stripe = provider(now)
    stripe.PaymentIntent.retrieve.side_effect = lambda pi: {"latest_charge": "ch-" + pi}
    stripe.Charge = SimpleNamespace(retrieve=Mock(side_effect=lambda charge: {"amount_refunded": 150 if charge == "ch-pi-renewal" else 100 if charge == "ch-pi-initial" else 0}))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: stripe)
    allowance = TokenEntry.objects.create(account=entry.account, kind="grant", amount=30, remaining=30, reference="stripe:personal-sub:renewal", app="cinema")
    request = RequestFactory().get("/"); request.user = user
    with override_settings(DCM_BILLING_APP="cinema"):
        response = withdrawal_preview(request, contract.purchase_id)
        import json
        data = json.loads(response.content)
        assert response.status_code == 200
        assert (data["amount_paid_cents"], data["refunded_cents"], data["refund_cents"]) == (1900, 250, expected)
        declaration = Declaration.objects.create(app="cinema", user=user, contract=contract, kind="withdrawal", channel="account", received_at=now)
        with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
    allowance.refresh_from_db(); entry.refresh_from_db(); declaration.refresh_from_db()
    assert declaration.status == "effective"
    assert sum(call.kwargs["amount"] for call in stripe.Refund.create.call_args_list) == expected
    assert (allowance.remaining, entry.remaining) == (30, 50)
