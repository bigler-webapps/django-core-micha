import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from django_core_micha.billing.config import load_billing_config
from django_core_micha.billing.models import ObjectAccessGrant, ObjectBillingSubject, ObjectCheckoutAttempt, ObjectSubscription


def catalogue(tmp_path, *, interval="month", limit="5"):
    path = tmp_path / "billing.yaml"
    path.write_text(f"""app: cinema
currency: chf
token_rates: {{}}
plans:
  - key: project
    kind: object_subscription
    name: Project
    amount_cents: 1000
    interval: {interval}
    member_limit: {limit}
    tax_code: txcd_10000000
""")
    return path


@pytest.mark.parametrize("interval", ["month", "year"])
def test_object_catalogue(tmp_path, interval):
    with override_settings(DCM_BILLING_APP="cinema", DCM_BILLING_FILE=catalogue(tmp_path, interval=interval)):
        plan = load_billing_config().plans[0]
    assert (plan.kind, plan.interval, plan.member_limit, plan.token_allowance) == ("object_subscription", interval, 5, 0)


@pytest.mark.parametrize("interval,limit", [("week", "5"), ("month", "0"), ("month", "-1"), ("month", "true")])
def test_object_catalogue_rejects_invalid_values(tmp_path, interval, limit):
    with override_settings(DCM_BILLING_APP="cinema", DCM_BILLING_FILE=catalogue(tmp_path, interval=interval, limit=limit)), pytest.raises(ValueError):
        load_billing_config()


@pytest.mark.django_db
def test_subject_and_independent_blocking_records():
    user = get_user_model().objects.create_user(username="payer")
    subject = ObjectBillingSubject.objects.create(app="one", object_key="same")
    other = ObjectBillingSubject.objects.create(app="two", object_key="same")
    with pytest.raises(IntegrityError), transaction.atomic():
        ObjectBillingSubject.objects.create(app="one", object_key="same")
    subscription = ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="project", stripe_subscription_id="sub_1", status="active")
    with pytest.raises(IntegrityError), transaction.atomic():
        ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="project", stripe_subscription_id="sub_2", status="active")
    ObjectSubscription.objects.create(subject=other, payer=user, plan_key="project", stripe_subscription_id="sub_3", status="active")
    subscription.blocks_purchase = False
    subscription.save()
    ObjectSubscription.objects.create(subject=subject, payer=user, plan_key="project", stripe_subscription_id="sub_4", status="active")
    attempt = ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="one", plan_key="project", idempotency_key="one", stripe_price_id="price")
    with pytest.raises(IntegrityError), transaction.atomic():
        ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="two", plan_key="project", idempotency_key="two", stripe_price_id="price")
    attempt.status = "failed"
    attempt.save()
    ObjectCheckoutAttempt.objects.create(subject=subject, payer=user, reference="two", plan_key="project", idempotency_key="two", stripe_price_id="price")


@pytest.mark.django_db
def test_grant_retains_audited_revocation():
    user = get_user_model().objects.create_user(username="operator")
    subject = ObjectBillingSubject.objects.create(app="app", object_key="object")
    with pytest.raises(IntegrityError), transaction.atomic():
        ObjectAccessGrant.objects.create(subject=subject, granted_by=user, reason="")
    grant = ObjectAccessGrant.objects.create(subject=subject, granted_by=user, reason="support")
    with pytest.raises(IntegrityError), transaction.atomic():
        ObjectAccessGrant.objects.create(subject=subject, granted_by=user, reason="again")
    grant.revoked_at = timezone.now()
    grant.revoked_by = user
    grant.revocation_reason = "ended"
    grant.save()
    ObjectAccessGrant.objects.create(subject=subject, granted_by=user, reason="renewed")
