from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model

from django_core_micha.billing.models import ObjectBillingSubject, ObjectCheckoutAttempt
from django_core_micha.billing.object_checkout import reconcile_object_attempt


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["unique", "duplicate", "foreign", "empty", "reference", "app", "payer", "plan", "mode"])
def test_lost_session_lookup_requires_unique_matching_customer_and_metadata(monkeypatch, case):
    user = get_user_model().objects.create_user(username="lookup")
    subject = ObjectBillingSubject.objects.create(app="cinema", object_key="film")
    attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="ref",
        plan_key="film", idempotency_key="key", stripe_customer_id="cus", stripe_price_id="price")
    session = {"id": "cs_one", "customer": "cus", "mode": "subscription", "status": "open",
        "url": "https://checkout.stripe.test/one", "metadata": {"app": "cinema", "user_id": str(user.pk),
        "plan_key": "film", "object_checkout_attempt": "ref"}}
    items = [session]
    if case == "duplicate":
        items.append(dict(session, id="cs_two"))
    elif case == "foreign":
        items = [dict(session, customer="cus_other")]
    elif case == "empty":
        items = []
    elif case in ("reference", "app", "payer", "plan"):
        key = {"reference": "object_checkout_attempt", "app": "app", "payer": "user_id", "plan": "plan_key"}[case]
        items = [dict(session, metadata=dict(session["metadata"], **{key: "mismatch"}))]
    elif case == "mode":
        items = [dict(session, mode="payment")]
    listing = Mock(return_value=SimpleNamespace(auto_paging_iter=lambda: iter(items)))
    retrieve = Mock(return_value=session)
    monkeypatch.setattr("django_core_micha.billing.object_checkout._stripe", lambda: SimpleNamespace(
        checkout=SimpleNamespace(Session=SimpleNamespace(list=listing, retrieve=retrieve))))
    assert reconcile_object_attempt(attempt.pk) is False
    attempt.refresh_from_db()
    assert attempt.stripe_session_id == ("cs_one" if case == "unique" else "")
    assert attempt.stripe_checkout_url == (session["url"] if case == "unique" else "")
    listing.assert_called_once_with(customer="cus", limit=100)
    assert retrieve.call_count == (1 if case == "unique" else 0)
