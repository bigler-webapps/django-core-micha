from datetime import timedelta
from types import SimpleNamespace
import json
import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.utils import timezone
from django_core_micha.billing.models import Purchase, Subscription, TeamPackage, TokenEntry
from django_core_micha.billing.services import access_state, grant_tokens, credit_purchase, debit_tokens, reverse_debit
from django_core_micha.billing.stripe_service import process_stripe_event
from django_core_micha.billing.views import withdrawal_execute, withdrawal_preview
from django_core_micha.billing.withdrawal import withdraw

pytestmark=pytest.mark.django_db

def user(i): return get_user_model().objects.create_user(username=f"withdraw-{i}",email=f"withdraw-{i}@example.com",password="x")

def purchase(u,kind="subscription",key="p",**kw):
    now=timezone.now()
    vals=dict(user=u,kind=kind,plan_key=key,stripe_object_id=f"obj-{u.pk}-{key}",stripe_payment_intent_id=f"pi-{u.pk}-{key}",amount_paid_cents=1000,currency="eur",billing_country="DE",purchased_at=now-timedelta(days=1),term_start=now-timedelta(days=1),term_end=now+timedelta(days=29),grant_references=[],tokens_granted=0)
    vals.update(kw); return Purchase.objects.create(**vals)

def stripe_mock(monkeypatch, *, fail_refund=False):
    calls={"cancel":[],"refund":[]}
    def cancel(sid): calls["cancel"].append(sid); return {"id":sid}
    def refund(**kwargs):
        calls["refund"].append(kwargs)
        if fail_refund and len(calls["refund"])==1: raise RuntimeError("provider unavailable")
        return SimpleNamespace(id="re_123")
    stripe=SimpleNamespace(Subscription=SimpleNamespace(cancel=cancel),Refund=SimpleNamespace(create=refund))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    return calls

def test_subscription_withdrawal_idempotent_access_tokens_and_email(monkeypatch, django_capture_on_commit_callbacks):
    u=user(1); now=timezone.now(); sub=Subscription.objects.create(user=u,plan_key="monthly",stripe_subscription_id="sub-one",status="active",current_period_start=now-timedelta(days=1),current_period_end=now+timedelta(days=29))
    g=grant_tokens(u,100,reference="allowance-one",purpose="term"); p=purchase(u,stripe_object_id="sub-one",stripe_payment_intent_id="pi-one",grant_references=["allowance-one"],tokens_granted=100)
    calls=stripe_mock(monkeypatch)
    with override_settings(DCM_BILLING_APP="maana",DCM_BILLING_WITHDRAWAL_ENABLED=True), django_capture_on_commit_callbacks(execute=True):
        calc,already=withdraw(p,now)
        calc2,already2=withdraw(p,now)
    assert not already and already2 and len(calls["cancel"])==1 and len(calls["refund"])==1
    assert calls["refund"][0]["amount"]==calc.refund_cents and calls["refund"][0]["idempotency_key"]==f"dcm-withdrawal:maana:{p.pk}"
    assert Subscription.objects.get(pk=sub.pk).status=="canceled" and access_state(u).status=="read_only"
    g.refresh_from_db(); assert g.remaining==0 and TokenEntry.objects.filter(kind="expiry",reference=f"withdrawal:{p.pk}:{g.pk}").exists()
    assert len(mail.outbox)==1 and Purchase.objects.get(pk=p.pk).withdrawal_email_sent

def test_refund_failure_can_retry_and_other_user_is_hidden(monkeypatch):
    u=user(2); p=purchase(u); calls=stripe_mock(monkeypatch,fail_refund=True)
    with override_settings(DCM_BILLING_APP="app"):
        with pytest.raises(RuntimeError): withdraw(p)
        p.refresh_from_db(); assert p.status=="active"
        withdraw(p)
    assert len(calls["refund"])==2 and calls["refund"][0]["idempotency_key"]==calls["refund"][1]["idempotency_key"]
    stranger=user(3); rf=RequestFactory(); req=rf.get("/withdrawal/1/preview/"); req.user=stranger
    with pytest.raises(Http404): withdrawal_preview(req,p.pk)

def test_team_and_token_package_execution_and_zero_refund_skips_stripe(monkeypatch):
    u=user(4); now=timezone.now(); pkg=TeamPackage.objects.create(owner=u,plan_key="team",seats=2,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=30),stripe_ref="team-ref")
    team=purchase(u,"team_package","team",stripe_object_id="team-ref",term_start=now-timedelta(days=1),term_end=now+timedelta(days=29))
    token=purchase(u,"token_package","tokens",stripe_object_id="token-ref",stripe_payment_intent_id="pi-token",amount_paid_cents=1000,term_start=None,term_end=None)
    calls=stripe_mock(monkeypatch)
    withdraw(team,now); withdraw(token,now)
    pkg.refresh_from_db(); assert pkg.ends_at==now and len(calls["refund"])==2

def test_views_confirm_and_withdrawn_post_returns_stored_result(monkeypatch):
    u=user(5); p=purchase(u); stripe_mock(monkeypatch)
    rf=RequestFactory(); req=rf.post("/withdrawal/",data=json.dumps({"confirm":True}),content_type="application/json"); req.user=u
    with override_settings(DCM_BILLING_APP="app"):
        assert withdrawal_execute(req,p.pk).status_code==200
        response=withdrawal_execute(req,p.pk)
    assert json.loads(response.content)["already_withdrawn"] is True

def test_reverse_debit_exactly_once_and_unknown_reference():
    u=user(6); credit_purchase(u,50,reference="bought"); debit_tokens(u,12,purpose="failed action",reference="action-1")
    first=reverse_debit(u,reference="action-1",reason="provider timeout")
    second=reverse_debit(u,reference="action-1",reason="retry")
    assert first.pk==second.pk and first.amount==12 and first.remaining==12
    with pytest.raises(ValueError,match="unknown_debit"): reverse_debit(u,reference="missing",reason="fault")
    with pytest.raises(ValueError): reverse_debit(u,reference="action-1",reason="")

def test_webhooks_cannot_reactivate_withdrawn_purchase_and_refund_is_noop(monkeypatch):
    u=user(7); p=purchase(u); p.status="withdrawn"; p.save(update_fields=("status",))
    stripe_mock(monkeypatch)
    with override_settings(DCM_BILLING_APP="app"):
        assert process_stripe_event({"type":"charge.refunded","data":{"object":{"metadata":{"app":"app"}}}})=="processed"
        event={"type":"customer.subscription.updated","data":{"object":{"id":p.stripe_object_id,"metadata":{"app":"app","plan_key":"monthly","user_id":str(u.pk)},"status":"active","current_period_start":int(timezone.now().timestamp()),"current_period_end":int((timezone.now()+timedelta(days=30)).timestamp())}}}
        process_stripe_event(event)
    assert not Subscription.objects.filter(stripe_subscription_id=p.stripe_object_id).exists()

def test_checkout_replay_creates_one_purchase_and_one_token_credit(tmp_path):
    import yaml
    from django.test import override_settings
    from django_core_micha.billing.models import Purchase
    cfg={"app":"app","currency":"usd","plans":[{"key":"tokens_10","kind":"token_package","name":"10 tokens","amount_cents":500,"tokens":10,"tax_code":"txcd_10103001"}],"token_rates":{}}
    path=tmp_path/"billing.yaml"; path.write_text(yaml.safe_dump(cfg),encoding="utf-8")
    u=user(8)
    event={"id":"evt-checkout","type":"checkout.session.completed","created":int(timezone.now().timestamp()),"data":{"object":{"id":"cs_one","payment_intent":"pi_one","payment_status":"paid","amount_total":500,"currency":"usd","metadata":{"app":"app","plan_key":"tokens_10","user_id":str(u.pk),"tokens":"10"},"customer_details":{"address":{"country":"DE"},"tax_ids":[]}}}}
    with override_settings(DCM_BILLING_APP="app",DCM_BILLING_FILE=str(path)):
        process_stripe_event(event); process_stripe_event(event)
    assert Purchase.objects.filter(stripe_object_id="pi_one").count()==1
    assert TokenEntry.objects.filter(kind="purchase",reference="stripe:checkout:cs_one").count()==1
