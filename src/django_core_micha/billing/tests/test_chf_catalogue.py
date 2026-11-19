from types import SimpleNamespace

import pytest
from django.test import override_settings

from django_core_micha.billing import annual_term_reminders, stripe_service
from django_core_micha.billing.config import load_billing_config
from django_core_micha.billing.contracts import contract_summary
from django_core_micha.billing.tests.test_annual_term_reminders import make_contract
from django_core_micha.billing.tests.test_plan_price_reconciliation import (
    add_remote, config, configured, map_price, use_config,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('currency', ['usd', 'chf'])
def test_catalogue_loads_supported_currency(tmp_path, currency):
    path = config(tmp_path, currency=currency)
    with use_config('maana', path):
        assert load_billing_config().currency == currency


@pytest.mark.parametrize('currency', ['eur', 'CHF'])
def test_catalogue_rejects_unsupported_currency(tmp_path, currency):
    path = config(tmp_path, currency=currency)
    with use_config('maana', path), pytest.raises(ValueError):
        load_billing_config()


@pytest.mark.parametrize('currency', ['usd', 'chf'])
def test_sync_creates_and_verifies_catalogue_currency(tmp_path, monkeypatch, currency):
    path, fake = configured(tmp_path, monkeypatch, currency=currency)
    with use_config('maana', path):
        assert stripe_service.sync_plans() == [('monthly', 'created:v1')]
        assert fake.price_creates[0]['currency'] == currency
        assert stripe_service.sync_plans() == [('monthly', 'unchanged')]
        fake.prices[0].currency = 'eur'
        with pytest.raises(stripe_service.PlanSyncError, match='currency'):
            stripe_service.sync_plans()


def test_currency_change_versions_price_and_checkout_uses_new_price(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch, currency='chf')
    add_remote(fake)
    old = map_price()
    with use_config('maana', path):
        assert stripe_service.sync_plans() == [('monthly', 'created:v2')]
        price = stripe_service._latest_price('monthly')
        params = stripe_service.checkout_session_params(plan=load_billing_config().plans[0],
            price=price, user=SimpleNamespace(pk=7), customer_id='cus_1')
    assert old.stripe_price_id == 'price_1'
    assert price.version == 2 and price.stripe_price_id != old.stripe_price_id
    assert fake.price_creates[0]['currency'] == 'chf'
    assert params['line_items'] == [{'price': price.stripe_price_id, 'quantity': 1}]


@pytest.mark.parametrize('currency', ['usd', 'chf'])
def test_contract_reminder_and_invoice_currency(tmp_path, currency):
    path = config(tmp_path, app='app', currency=currency)
    contract = make_contract()
    payment = contract.payments.get()
    payment.currency = currency
    payment.save(update_fields=['currency'])
    with use_config('app', path), override_settings(DCM_BILLING_CANCELLATION_URL='https://example.test/cancel',
                                                    DEFAULT_FROM_EMAIL='billing@example.test'):
        assert contract_summary(contract)['next_charge']['currency'] == currency
        payload = annual_term_reminders._payload(contract, annual_term_reminders._currency(contract))
        assert f'7.25 {currency.upper()}' in payload['text']
        recorded = stripe_service._record_contract_payment(contract, {'id': 'in-fallback', 'amount_paid': 100}, 'continuation')
        assert recorded.currency == currency
        other = 'usd' if currency == 'chf' else 'chf'
        contract.payments.update(currency=other)
        assert stripe_service._record_contract_payment(contract, {'id': 'in-kept', 'amount_paid': 100}, 'continuation').currency == other
        explicit = stripe_service._record_contract_payment(contract, {'id': 'in-explicit', 'currency': 'EUR'}, 'continuation')
        assert explicit.currency == 'eur'
