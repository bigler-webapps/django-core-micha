from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from django_core_micha.billing.config import Plan
from django_core_micha.billing.models import ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription, Purchase
from django_core_micha.billing.object_lifecycle import handle_object_event

@pytest.mark.django_db
@pytest.mark.parametrize('status', ['open', 'failed', 'expired'])
def test_reconcile_paid_invoice_without_original_event(monkeypatch, status):
    user = get_user_model().objects.create_user(username='payer')
    subject = ObjectBillingSubject.objects.create(app='cinema', object_key='film')
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference='ref', plan_key='film', idempotency_key='key', stripe_price_id='price', stripe_customer_id='cus', status=status)
    plan = Plan(key='film', kind='object_subscription', name='Film', amount_cents=1000, interval='month', member_limit=5, tax_code='tax')
    cfg = SimpleNamespace(app='cinema', plans=[plan], currency='chf', contract=None)
    monkeypatch.setattr('django_core_micha.billing.stripe_service.load_billing_config', lambda: cfg)
    monkeypatch.setattr('django_core_micha.billing.contracts.load_billing_config', lambda: cfg)
    meta = {'app': 'cinema', 'user_id': str(user.pk), 'plan_key': 'film', 'object_checkout_attempt': 'ref'}
    now = int(timezone.now().timestamp())
    invoice = {'id': 'in_1', 'subscription': 'sub_1', 'status': 'paid', 'period_start': now, 'period_end': now + 3600, 'amount_paid': 1000, 'currency': 'chf', 'billing_reason': 'subscription_create'}
    remote = {'id': 'sub_1', 'customer': 'cus', 'metadata': meta, 'status': 'active', 'latest_invoice': 'in_1'}
    provider = SimpleNamespace(Subscription=SimpleNamespace(retrieve=Mock(return_value=remote)),
        Invoice=SimpleNamespace(retrieve=Mock(return_value=invoice), list=Mock(return_value=SimpleNamespace(data=[invoice]))))
    monkeypatch.setattr('django_core_micha.billing.stripe_service._stripe', lambda: provider)
    with override_settings(DCM_BILLING_APP='cinema'):
        for _ in range(2):
            assert handle_object_event('customer.subscription.updated', remote, meta, {})
        assert ObjectSubscription.objects.count() == 1
        assert Purchase.objects.count() == 1
        Purchase.objects.update(status='withdrawn')
        handle_object_event('customer.subscription.updated', remote, meta, {})
        assert Purchase.objects.get().status == 'withdrawn'
        assert ObjectSubscription.objects.count() == 1


@pytest.mark.django_db
def test_object_attempt_failure_does_not_block_other_reconciliation(monkeypatch, settings):
    from io import StringIO
    from django.core.management import call_command
    from django.core.management.base import CommandError
    user = get_user_model().objects.create_user(username="reconcile")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="ref", plan_key="film",
        idempotency_key="key", stripe_price_id="price", stripe_session_id="cs_bad")
    settings.DCM_BILLING_APP = "cinema"
    pager = lambda rows: SimpleNamespace(auto_paging_iter=lambda: iter(rows))
    provider = SimpleNamespace(Subscription=SimpleNamespace(list=Mock(return_value=pager([
        {"id": "personal", "metadata": {"app": "cinema"}},
        {"id": "team", "metadata": {"app": "cinema"}}]))),
        Event=SimpleNamespace(list=Mock(return_value=pager([]))),
        Refund=SimpleNamespace(list=Mock(return_value=pager([]))))
    monkeypatch.setattr("django_core_micha.billing.management.commands.billing_reconcile._stripe", lambda: provider)
    reconcile = Mock(side_effect=ConnectionError("unavailable"))
    monkeypatch.setattr("django_core_micha.billing.object_checkout.reconcile_object_attempt", reconcile)
    process = Mock()
    monkeypatch.setattr("django_core_micha.billing.management.commands.billing_reconcile.process_stripe_event", process)
    with pytest.raises(CommandError, match="object checkout reconciliation failed"):
        call_command("billing_reconcile", stdout=StringIO())
    assert [c.args[0]["data"]["object"]["id"] for c in process.call_args_list] == ["personal", "team"]
    assert provider.Event.list.called and provider.Refund.list.called
