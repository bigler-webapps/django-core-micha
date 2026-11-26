from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from django_core_micha.billing.models import ObjectBillingSubject, ObjectCheckoutAttempt


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["unique", "empty", "ambiguous", "missing_customer", "provider_failure"])
def test_command_recovers_lost_session_and_continues_other_reconciliation(monkeypatch, settings, case):
    user = get_user_model().objects.create_user(username="command-payer")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    attempt = ObjectCheckoutAttempt.objects.create(
        subject=subject, payer=user, reference="ref", plan_key="film", idempotency_key="key",
        stripe_customer_id="" if case == "missing_customer" else "cus", stripe_price_id="price",
    )
    settings.DCM_BILLING_APP = "cinema"
    session = {"id": "cs_one", "customer": "cus", "mode": "subscription", "status": "open",
        "url": "https://checkout.stripe.test/one", "metadata": {"app": "cinema", "user_id": str(user.pk),
        "plan_key": "film", "object_checkout_attempt": "ref"}}
    rows = [] if case == "empty" else [session]
    if case == "ambiguous":
        rows.append(dict(session, id="cs_two"))
    pager = lambda items: SimpleNamespace(auto_paging_iter=lambda: iter(items))
    listing = Mock(return_value=pager(rows))
    if case == "provider_failure":
        listing.side_effect = ConnectionError("unavailable")
    retrieve = Mock(return_value=session)
    checkout = SimpleNamespace(Session=SimpleNamespace(list=listing, retrieve=retrieve))
    provider = SimpleNamespace(checkout=checkout,
        Subscription=SimpleNamespace(list=Mock(return_value=pager([{"id": "sub", "metadata": {"app": "cinema"}}]))),
        Event=SimpleNamespace(list=Mock(return_value=pager([{"id": "evt", "type": "example"}]))),
        Refund=SimpleNamespace(list=Mock(return_value=pager([]))))
    monkeypatch.setattr("django_core_micha.billing.management.commands.billing_reconcile._stripe", lambda: provider)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: provider)
    process = Mock(return_value="processed")
    monkeypatch.setattr("django_core_micha.billing.management.commands.billing_reconcile.process_stripe_event", process)
    if case == "provider_failure":
        with pytest.raises(CommandError, match="object checkout reconciliation failed"):
            call_command("billing_reconcile", stdout=StringIO())
    else:
        call_command("billing_reconcile", stdout=StringIO())
    attempt.refresh_from_db()
    assert attempt.stripe_session_id == ("cs_one" if case == "unique" else "")
    assert attempt.stripe_checkout_url == (session["url"] if case == "unique" else "")
    assert listing.call_count == (0 if case == "missing_customer" else 1)
    assert retrieve.call_count == (1 if case == "unique" else 0)
    assert process.call_count == 2
    assert provider.Refund.list.called
