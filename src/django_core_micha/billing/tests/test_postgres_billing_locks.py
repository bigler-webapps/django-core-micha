from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import override_settings
from django.utils import timezone

from django_core_micha.billing.contracts import process_refund
from django_core_micha.billing.declarations import (
    _confirmation_token, confirm_public_declaration, create_owner_declaration,
)
from django_core_micha.billing.models import (
    AICostRecord, Contract, ContractPayment, Declaration, Purchase, Refund,
)
from django_core_micha.billing.services import (
    debit_tokens, grant_tokens, reverse_debit, settle_ai_usage, token_balance,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return get_user_model().objects.create_user(username="billing-lock-owner", email="owner@example.test")


@pytest.fixture
def contract(owner):
    return Contract.objects.create(user=owner, app="app", kind="subscription", plan_key="plan", stripe_object_id="sub-lock")


@pytest.mark.parametrize("has_purchase", [False, True])
def test_owner_declaration_locks_contract_with_optional_purchase(owner, contract, has_purchase, monkeypatch):
    if has_purchase:
        contract.purchase = Purchase.objects.create(user=owner, kind="subscription", plan_key="plan",
            stripe_object_id="purchase-lock", amount_paid_cents=100, currency="eur", purchased_at=timezone.now())
        contract.save(update_fields=("purchase",))
    monkeypatch.setattr("django_core_micha.billing.contracts.apply_cancellation", lambda declaration: declaration)
    result = create_owner_declaration(contract, "cancellation")
    assert result.contract_id == contract.pk
    assert result.user_id == owner.pk


def _assert_locked_by_another_transaction(model, pk):
    """Probe an active transaction from a separate PostgreSQL connection."""
    def probe():
        close_old_connections()
        try:
            try:
                with transaction.atomic():
                    model.objects.select_for_update(nowait=True).get(pk=pk)
            except DatabaseError as exc:
                cause = exc.__cause__
                return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(probe).result(timeout=10) == "55P03"


def _require_postgresql():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock semantics required")


@pytest.mark.django_db(transaction=True)
def test_owner_declaration_locks_related_purchase(owner, contract, monkeypatch):
    _require_postgresql()
    purchase = Purchase.objects.create(user=owner, kind="subscription", plan_key="plan",
        stripe_object_id="purchase-probe", amount_paid_cents=100, currency="eur", purchased_at=timezone.now())
    contract.purchase = purchase
    contract.save(update_fields=("purchase",))
    create = Declaration.objects.create
    probes = []

    def create_with_probe(**kwargs):
        _assert_locked_by_another_transaction(Purchase, purchase.pk)
        probes.append(True)
        return create(**kwargs)

    monkeypatch.setattr(Declaration.objects, "create", create_with_probe)
    monkeypatch.setattr("django_core_micha.billing.contracts.apply_cancellation", lambda declaration: declaration)
    create_owner_declaration(contract, "cancellation")
    assert probes == [True]


@pytest.mark.django_db(transaction=True)
def test_public_confirmation_locks_related_contract(contract, monkeypatch):
    _require_postgresql()
    declaration = Declaration.objects.create(app="app", kind="cancellation", channel="public",
        contract=contract, status="awaiting_confirmation")
    token = _confirmation_token(declaration)
    declaration.save(update_fields=("confirm_token_hash", "confirm_expires_at"))
    probes = []

    def apply_with_probe(declaration):
        _assert_locked_by_another_transaction(Contract, contract.pk)
        probes.append(True)
        return declaration

    monkeypatch.setattr("django_core_micha.billing.contracts.apply_cancellation", apply_with_probe)
    assert confirm_public_declaration(token) is not None
    assert probes == [True]


@pytest.mark.django_db(transaction=True)
def test_usage_settlement_locks_related_user(owner):
    _require_postgresql()
    row = AICostRecord.objects.create(user=owner, provider="test", model="test", purpose="workload",
        usage_status="reported", settlement_status="pending")
    probes = []

    def settle_with_probe(user, usage):
        _assert_locked_by_another_transaction(get_user_model(), owner.pk)
        probes.append(True)

    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=settle_with_probe):
        assert settle_ai_usage(row.pk)
    assert probes == [True]


def test_public_confirmation_locks_optional_contract(contract, monkeypatch):
    declaration = Declaration.objects.create(app="app", kind="cancellation", channel="public",
        contract=contract, status="awaiting_confirmation")
    token = _confirmation_token(declaration)
    declaration.save(update_fields=("confirm_token_hash", "confirm_expires_at"))
    monkeypatch.setattr("django_core_micha.billing.contracts.apply_cancellation", lambda declaration: declaration)
    confirmed = confirm_public_declaration(token)
    assert confirmed.confirmed_at is not None
    assert confirmed.user_id == contract.user_id
    assert confirm_public_declaration(token) is None


def test_usage_settlement_debits_once_retries_and_refunds(owner):
    grant_tokens(owner, 20, reference="lock-grant", purpose="test")
    row = AICostRecord.objects.create(user=owner, provider="test", model="test", purpose="workload",
        reference="lock-usage", source_id="test", usage_status="reported", settlement_status="pending")
    calls = []

    def settle(user, usage):
        calls.append(usage.pk)
        debit_tokens(user, 5, purpose="test", reference=usage.reference)

    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=settle):
        assert settle_ai_usage(row.pk)
        assert settle_ai_usage(row.pk)
    assert calls == [row.pk]
    assert token_balance(owner) == 15
    reverse_debit(owner, reference=row.reference, reason="test")
    assert token_balance(owner) == 20


def test_usage_without_user_remains_pending():
    row = AICostRecord.objects.create(user=None, provider="test", model="test", purpose="workload",
        usage_status="reported", settlement_status="pending")
    assert settle_ai_usage(row.pk) is False
    row.refresh_from_db()
    assert row.settlement_status == "pending"


def test_failed_usage_settlement_remains_pending_for_retry(owner):
    row = AICostRecord.objects.create(user=owner, provider="test", model="test", purpose="workload",
        usage_status="reported", settlement_status="pending")

    def fail(user, usage):
        raise ValueError("tariff unavailable")

    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=fail), pytest.raises(ValueError):
        settle_ai_usage(row.pk)
    row.refresh_from_db()
    assert row.settlement_status == "pending"
    with override_settings(DCM_AI_USAGE_SETTLEMENT_HOOK=lambda user, usage: None):
        assert settle_ai_usage(row.pk)


def test_contract_refund_updates_payment_once(contract, monkeypatch):
    payment = ContractPayment.objects.create(contract=contract, stripe_payment_intent_id="pi-lock",
        amount_paid_cents=100, currency="eur")
    refund = Refund.objects.create(contract=contract, payment=payment, amount_cents=100, currency="eur",
        idempotency_key="refund-lock", due_by=timezone.now() + timedelta(days=14))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe", lambda: SimpleNamespace(
        Refund=SimpleNamespace(create=lambda **kwargs: {"id": "re-lock", "status": "succeeded"})))
    assert process_refund(refund.pk).status == "succeeded"
    assert process_refund(refund.pk).status == "succeeded"
    payment.refresh_from_db()
    assert payment.refunded_cents == 100
