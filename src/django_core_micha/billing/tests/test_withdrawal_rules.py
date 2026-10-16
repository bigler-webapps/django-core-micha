from datetime import timedelta
import pytest
from django.test import override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django_core_micha.billing.models import Purchase
from django_core_micha.billing.services import credit_purchase
from django_core_micha.billing.withdrawal import eligibility, refund_amount

pytestmark=pytest.mark.django_db

def make_purchase(**kwargs):
    n=get_user_model().objects.count()
    user=get_user_model().objects.create_user(username=f"withdraw-rules-{n}",email=f"rules{n}@example.com")
    now=timezone.now()
    defaults=dict(user=user,kind="subscription",plan_key="monthly",stripe_object_id="sub_rules",amount_paid_cents=1001,currency="eur",billing_country="DE",has_business_tax_id=False,purchased_at=now-timedelta(days=14),term_start=now-timedelta(days=4),term_end=now+timedelta(days=6),grant_references=[],tokens_granted=0)
    defaults.update(kwargs)
    return Purchase.objects.create(**defaults),now

def test_eligibility_country_tax_status_window_and_setting():
    p,now=make_purchase()
    assert eligibility(p,now).eligible
    assert eligibility(p,now+timedelta(seconds=1)).reason=="period_over"
    p.billing_country="CH"; assert eligibility(p,now).reason=="country_not_eligible"
    p.billing_country="FR"; p.has_business_tax_id=True; assert eligibility(p,now).reason=="business_purchase"
    p.has_business_tax_id=False; p.status="withdrawn"; assert eligibility(p,now).reason=="not_active"
    with override_settings(DCM_BILLING_WITHDRAWAL_ENABLED=False): assert eligibility(p,now).reason=="withdrawal_disabled"

def test_refund_time_token_max_package_zero_tokens_cap_and_rounding():
    start=timezone.now(); p,now=make_purchase(stripe_object_id="sub-time",term_start=start,term_end=start+timedelta(days=3),tokens_granted=0)
    assert refund_amount(p,start+timedelta(days=1)).time_share_cents==334
    p,now=make_purchase(stripe_object_id="sub_round",amount_paid_cents=1001,term_start=timezone.now(),term_end=timezone.now()+timedelta(days=3),tokens_granted=3,grant_references=["grant-round"])
    credit_purchase(p.user,3,reference="grant-round")
    entry=p.user.billing_token_accounts.get().entries.get(kind="purchase"); entry.remaining=2; entry.save(update_fields=("remaining",))
    calc=refund_amount(p,timezone.now()+timedelta(seconds=1))
    assert calc.token_share_cents==334 and calc.refund_cents==667
    calc=refund_amount(p,timezone.now()+timedelta(seconds=1))
    assert calc.token_share_cents==334 and calc.used_cents==334
    p.kind="token_package"; p.term_start=timezone.now()-timedelta(days=100); p.term_end=timezone.now()-timedelta(days=1)
    assert refund_amount(p,timezone.now()).time_share_cents==0
    p.tokens_granted=0
    assert refund_amount(p,timezone.now()).refund_cents==1001
    p.kind="subscription"; p.tokens_granted=0; p.term_start=timezone.now()-timedelta(days=10); p.term_end=timezone.now()-timedelta(days=1)
    capped=refund_amount(p,timezone.now())
    assert capped.used_cents==1001 and capped.refund_cents==0
