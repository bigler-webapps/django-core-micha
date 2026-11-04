from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from django_core_micha.billing.config import load_billing_config
from django_core_micha.billing.contracts import (_cancellation_effective, apply_cancellation, apply_withdrawal,
    apply_due_cancellations, contract_summary, create_continuation_schedule, run_transition_guard,
    withdrawal_deadline, withdrawal_eligibility)
from django_core_micha.billing.models import (Contract, ContractPayment, Declaration, Purchase, StripePrice,
    Refund, Subscription, TokenAccount, TokenEntry)
from django_core_micha.billing.services import token_balance
from django_core_micha.billing.stripe_service import process_stripe_event

pytestmark=pytest.mark.django_db


@pytest.fixture
def billing_config(request,monkeypatch):
    path=Path.cwd()/f".billing-test-{uuid4().hex}.yaml"
    path.write_text("""app: app
currency: usd
token_rates: {}
contract:
  terms_version: '2026-11-04'
  seller: 'Example Seller Ltd'
  withdrawal_refund: full
  public_declarations: true
  transition_guard_days: 7
plans:
  - key: annual
    kind: subscription
    name: Annual
    amount_cents: 5000
    interval: year
    token_allowance: 1200
    tax_code: txcd_10000000
    continuation: monthly
  - key: monthly
    kind: continuation
    name: Monthly continuation
    amount_cents: 500
    interval: month
    token_allowance: 100
    tax_code: txcd_10000000
  - key: one_time
    kind: token_package
    name: Tokens
    amount_cents: 1000
    tokens: 100
    tax_code: txcd_10000000
    available: false
""",encoding="utf-8")
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_FILE",str(path),raising=False)
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_APP","app",raising=False)
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path


def user(i,email=None):
    return get_user_model().objects.create_user(username=f"contract-{i}",email=email or f"contract-{i}@example.com",password="x")


def create_contract(u,*,policy="full",status="active",**changes):
    now=timezone.now()
    purchase=Purchase.objects.create(user=u,kind="subscription",plan_key="annual",stripe_object_id=f"sub-{u.pk}",
        stripe_payment_intent_id=f"pi-{u.pk}",amount_paid_cents=5000,currency="usd",billing_country="DE",
        purchased_at=now,term_start=now,term_end=now+timedelta(days=365),grant_references=[],tokens_granted=1200)
    values={"user":u,"app":"app","kind":"subscription","plan_key":"annual","purchase":purchase,
        "stripe_object_id":purchase.stripe_object_id,"terms_version":"2026-11-04","seller":"Example Seller Ltd",
        "withdrawal_policy":policy,"consumer_status":"consumer","billing_country":"DE","started_at":now,
        "info_delivered_at":now,"initial_term_end":now+timedelta(days=365),"phase":"initial",
        "status":status,"continuation_plan_key":"monthly","continuation_stripe_price_id":"price-monthly",
        "continuation_amount_cents":500,"continuation_token_allowance":100,"continuation_interval":"month",
        "schedule_status":"pending"}
    values.update(changes)
    contract=Contract.objects.create(**values)
    Subscription.objects.create(user=u,plan_key="annual",stripe_subscription_id=purchase.stripe_object_id,
        status="active",current_period_start=now,current_period_end=now+timedelta(days=365))
    return contract,purchase


def test_config_continuation_is_validated_and_unavailable_plans_are_read(billing_config):
    cfg=load_billing_config()
    assert cfg.contract["withdrawal_refund"]=="full"
    assert cfg.plans[0].continuation=="monthly"
    assert cfg.plans[1].kind=="continuation" and cfg.plans[1].token_allowance==100
    assert cfg.plans[2].available is False
    from django_core_micha.billing.views import _plans
    assert {x["key"] for x in _plans()}=={"annual"}
    from django_core_micha.billing.stripe_service import checkout_session_params
    with pytest.raises(LookupError): checkout_session_params(plan=cfg.plans[1],price=None,user=None,customer_id="cus")
    with pytest.raises(LookupError): checkout_session_params(plan=cfg.plans[2],price=None,user=None,customer_id="cus")


def test_withdrawal_deadline_uses_delivery_or_extended_default():
    u=user(1); started=timezone.now()-timedelta(days=14)
    contract,_=create_contract(u,started_at=started,info_delivered_at=started)
    deadline=withdrawal_deadline(contract)
    assert withdrawal_eligibility(contract,deadline).eligible
    assert not withdrawal_eligibility(contract,deadline+timedelta(microseconds=1)).eligible
    contract.info_delivered_at=None
    from django_core_micha.billing.contracts import _add_months
    assert withdrawal_deadline(contract)==_add_months(started,12)+timedelta(days=14)
    contract.info_delivered_at=started+timedelta(days=30)
    assert withdrawal_deadline(contract)==contract.info_delivered_at+timedelta(days=14)
    contract.consumer_status="unknown"
    result=withdrawal_eligibility(contract,timezone.now())
    assert not result.eligible and result.reason=="review_required"


def test_extraordinary_cancellation_is_reviewed_and_ordinary_access_remains(monkeypatch,billing_config):
    u=user(2); contract,_=create_contract(u)
    declaration=Declaration.objects.create(app="app",kind="cancellation",channel="account",user=u,contract=contract,
        cancellation_type="extraordinary",received_at=timezone.now())
    calls=[]
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Subscription=SimpleNamespace(modify=lambda *a,**k:calls.append((a,k)))))
    apply_cancellation(declaration)
    declaration.refresh_from_db(); contract.refresh_from_db()
    assert declaration.status=="needs_review" and declaration.received_at
    assert contract.needs_review and calls==[]
    declaration2=Declaration.objects.create(app="app",kind="cancellation",channel="account",user=u,contract=contract,cancellation_type="ordinary")
    apply_cancellation(declaration2)
    contract.refresh_from_db(); sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id)
    assert declaration2.status=="scheduled" and declaration2.effective_at==sub.current_period_end
    assert sub.status=="active" and sub.cancel_at_period_end
    assert calls and calls[0][1]["cancel_at_period_end"] is True


def test_requested_cancellation_preserves_end_of_month_and_leap_anchors():
    u=user(3); contract,_=create_contract(u)
    jan31=datetime(2027,1,31,tzinfo=dt_timezone.utc)
    contract.initial_term_end=jan31; contract.phase="continuation"
    sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id); sub.current_period_end=jan31; sub.save()
    assert _cancellation_effective(contract,datetime(2027,3,1).date()).date()==datetime(2027,3,31).date()
    leap=datetime(2028,2,29,tzinfo=dt_timezone.utc)
    sub.current_period_end=leap; sub.save()
    assert _cancellation_effective(contract,datetime(2028,4,1).date()).date()==datetime(2028,4,30).date()


def test_later_cancellation_is_applied_at_the_requested_month_end(monkeypatch,billing_config):
    u=user(13); contract,_=create_contract(u,phase="continuation")
    sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id)
    now=timezone.now(); sub.current_period_end=now+timedelta(days=15); sub.save(update_fields=("current_period_end",))
    requested=(sub.current_period_end+timedelta(days=20)).date()
    declaration=Declaration.objects.create(app="app",kind="cancellation",channel="account",user=u,contract=contract,
        cancellation_type="ordinary",requested_end_date=requested)
    calls=[]
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Subscription=SimpleNamespace(modify=lambda *a,**kw:calls.append((a,kw)))))
    apply_cancellation(declaration)
    assert declaration.requested_end_date==requested and declaration.effective_at>sub.current_period_end and calls==[]
    sub.current_period_end=declaration.effective_at; sub.save(update_fields=("current_period_end",))
    assert apply_due_cancellations(now=declaration.effective_at-timedelta(days=1))==1
    assert calls and calls[0][1]["cancel_at_period_end"] is True
    assert apply_due_cancellations(now=declaration.effective_at+timedelta(seconds=1))==1
    declaration.refresh_from_db(); contract.refresh_from_db()
    assert declaration.status=="effective" and contract.status=="ended"


def test_full_withdrawal_refunds_each_payment_and_only_unused_tokens(monkeypatch,billing_config,django_capture_on_commit_callbacks):
    u=user(4); contract,purchase=create_contract(u)
    first=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-first",stripe_payment_intent_id="pi-first",
        amount_paid_cents=1000,refunded_cents=200,currency="usd")
    second=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-second",stripe_payment_intent_id="pi-second",
        amount_paid_cents=600,currency="usd",billing_reason="continuation")
    account=TokenAccount.objects.create(user=u)
    TokenEntry.objects.create(account=account,kind="grant",amount=1200,remaining=0,reference=f"stripe:{contract.stripe_object_id}:1",app="app")
    TokenEntry.objects.create(account=account,kind="grant",amount=100,remaining=50,reference=f"stripe:{contract.stripe_object_id}:2",app="app")
    purchase.grant_references=[f"stripe:{contract.stripe_object_id}:1",f"stripe:{contract.stripe_object_id}:2"]; purchase.save(update_fields=("grant_references",))
    calls=[]
    def refund_create(**kwargs): calls.append(kwargs); return {"id":f"re-{len(calls)}","status":"succeeded"}
    def pi_retrieve(pi): return {"latest_charge":"ch-first" if pi=="pi-first" else "ch-second"}
    def charge_retrieve(ch): return {"amount_refunded":200 if ch=="ch-first" else 0}
    stripe=SimpleNamespace(Subscription=SimpleNamespace(cancel=lambda *a,**k:{"id":"sub"}),
        Refund=SimpleNamespace(create=refund_create),PaymentIntent=SimpleNamespace(retrieve=pi_retrieve),Charge=SimpleNamespace(retrieve=charge_retrieve))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
    declaration.refresh_from_db(); contract.refresh_from_db(); purchase.refresh_from_db()
    first.refresh_from_db(); second.refresh_from_db()
    assert declaration.status=="effective" and contract.status=="withdrawn" and purchase.status=="withdrawn"
    assert [c["amount"] for c in calls]==[800,600]
    assert all(c["metadata"]["dcm_refund"].startswith("dcm-refund:app:") for c in calls)
    assert first.refunded_cents==1000 and second.refunded_cents==600
    assert TokenEntry.objects.filter(account=account,kind="expiry").count()==1
    assert token_balance(u)==0


def test_schedule_has_two_phases_and_continuation_price_wins_over_metadata(monkeypatch,billing_config):
    u=user(5); contract,purchase=create_contract(u)
    StripePrice.objects.create(app="app",plan_key="monthly",version=1,lookup_key="monthly:v1",fingerprint="f",
        stripe_product_id="prod",stripe_price_id="price-monthly")
    start=int(timezone.now().timestamp()); end=start+365*86400
    calls={"create":[],"modify":[]}
    phase={"items":[{"price":"price-annual","quantity":1}],"start_date":start,"end_date":end}
    def create(**kwargs): calls["create"].append(kwargs); return {"id":"sched-1","phases":[phase]}
    def modify(sid,**kwargs): calls["modify"].append((sid,kwargs)); return {"id":sid}
    stripe=SimpleNamespace(SubscriptionSchedule=SimpleNamespace(create=create,modify=modify))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    create_continuation_schedule(contract.pk)
    contract.refresh_from_db()
    assert contract.schedule_status=="created" and len(calls["create"])==1
    phases=calls["modify"][0][1]["phases"]
    assert len(phases)==2 and phases[0]["items"]==phase["items"]
    assert phases[1]=={"items":[{"price":"price-monthly","quantity":1}],"duration":{"interval":"month","interval_count":1},"proration_behavior":"none",
        "metadata":{"app":"app","user_id":str(u.pk),"plan_key":"monthly"}}
    assert calls["modify"][0][1]["end_behavior"]=="release"
    item_start=start+365*86400; item_end=item_start+31*86400
    remote={"id":contract.stripe_object_id,"status":"active","metadata":{"app":"app","user_id":str(u.pk),"plan_key":"annual"},
        "items":{"data":[{"price":{"id":"price-monthly"}}]},"current_period_start":item_start,"current_period_end":item_end}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid:remote)))
    invoice={"id":"in-month-1","subscription":contract.stripe_object_id,"metadata":{"app":"app"},"amount_paid":500,"currency":"usd",
        "billing_reason":"subscription_cycle","lines":{"data":[{"price":{"id":"price-monthly"}}]}}
    assert process_stripe_event({"type":"invoice.paid","data":{"object":invoice}})=="processed"
    remote["current_period_start"]=item_end; remote["current_period_end"]=item_end+31*86400
    invoice["id"]="in-month-2"
    assert process_stripe_event({"type":"invoice.paid","data":{"object":invoice}})=="processed"
    contract.refresh_from_db()
    assert contract.phase=="continuation" and token_balance(u)==200
    assert ContractPayment.objects.filter(contract=contract,billing_reason="continuation").count()==2


def test_paid_checkout_creates_one_contract_payment_and_one_schedule(monkeypatch,billing_config,django_capture_on_commit_callbacks):
    u=user(7); now=timezone.now(); start=int(now.timestamp()); end=start+365*86400
    for key,price_id in (("annual","price-annual"),("monthly","price-monthly")):
        StripePrice.objects.create(app="app",plan_key=key,version=1,lookup_key=f"{key}:v1",fingerprint=key,
            stripe_product_id=f"prod-{key}",stripe_price_id=price_id)
    remote={"id":"sub-checkout","status":"active","metadata":{"app":"app","user_id":str(u.pk),"plan_key":"annual"},
        "items":{"data":[{"price":{"id":"price-annual"}}]},"current_period_start":start,"current_period_end":end,
        "latest_invoice":{"id":"in-checkout","amount_paid":5000,"payment_intent":"pi-checkout","currency":"usd","created":start}}
    calls=[]; phase={"items":[{"price":"price-annual","quantity":1}],"start_date":start,"end_date":end}
    stripe=SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid:remote),
        SubscriptionSchedule=SimpleNamespace(create=lambda **kw:(calls.append(("create",kw)) or {"id":"sched-checkout","phases":[phase]}),
            modify=lambda sid,**kw:(calls.append(("modify",sid,kw)) or {"id":sid})))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    session={"id":"cs-checkout","subscription":"sub-checkout","payment_status":"paid","amount_total":5000,"currency":"usd",
        "created":start,"customer_details":{"address":{"country":"DE"},"tax_ids":[]},
        "metadata":{"app":"app","plan_key":"annual","user_id":str(u.pk),"terms_version":"2026-11-04"}}
    event={"id":"evt-checkout","type":"checkout.session.completed","created":start,"data":{"object":session}}
    with django_capture_on_commit_callbacks(execute=True): process_stripe_event(event)
    with django_capture_on_commit_callbacks(execute=True): process_stripe_event(event)
    contract=Contract.objects.get(stripe_object_id="sub-checkout")
    assert contract.info_delivered_at==contract.started_at
    assert contract.schedule_status=="created" and contract.stripe_schedule_id=="sched-checkout"
    assert ContractPayment.objects.filter(contract=contract,stripe_invoice_id="in-checkout",amount_paid_cents=5000).count()==1
    assert len([x for x in calls if x[0]=="create"])==1


def test_refund_timeout_retries_fixed_row_amount_and_duplicate_events_complete_once(monkeypatch,billing_config):
    u=user(8); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-retry",stripe_payment_intent_id="pi-retry",
        amount_paid_cents=900,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    row=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=750,currency="usd",
        idempotency_key="dcm-refund:app:retry-1",due_by=declaration.received_at+timedelta(days=14))
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls)==1: raise TimeoutError()
        return {"id":"re-retry","status":"succeeded"}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Refund=SimpleNamespace(create=create)))
    from django_core_micha.billing.contracts import process_refund
    assert process_refund(row.pk).status=="pending"
    assert process_refund(row.pk).status=="succeeded"
    row.refresh_from_db(); payment.refresh_from_db()
    assert calls[0]["amount"]==calls[1]["amount"]==750
    assert calls[0]["idempotency_key"]==calls[1]["idempotency_key"]==row.idempotency_key
    assert payment.refunded_cents==750
    refund_event={"type":"refund.updated","data":{"object":{"id":"re-retry","status":"succeeded","amount":750,"currency":"usd",
        "payment_intent":"pi-retry","metadata":{"app":"app","contract_id":str(contract.pk),"dcm_refund":row.idempotency_key}}}}
    process_stripe_event(refund_event); process_stripe_event(refund_event)
    payment.refresh_from_db(); assert payment.refunded_cents==750 and row.status=="succeeded"


def test_late_invoice_after_withdrawal_does_not_reactivate_or_regrant(monkeypatch,billing_config):
    u=user(9); contract,purchase=create_contract(u,status="withdrawn")
    purchase.status="withdrawn"; purchase.save(update_fields=("status",))
    sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id); sub.status="canceled"; sub.save(update_fields=("status",))
    StripePrice.objects.create(app="app",plan_key="annual",version=1,lookup_key="annual:v1",fingerprint="annual",
        stripe_product_id="prod-annual",stripe_price_id="price-annual")
    remote={"id":contract.stripe_object_id,"status":"active","metadata":{"app":"app","user_id":str(u.pk),"plan_key":"annual"},
        "items":{"data":[{"price":{"id":"price-annual"}}]},"current_period_start":int(timezone.now().timestamp()),
        "current_period_end":int((timezone.now()+timedelta(days=365)).timestamp())}
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Subscription=SimpleNamespace(retrieve=lambda sid:remote)))
    event={"type":"invoice.paid","data":{"object":{"id":"in-late","subscription":contract.stripe_object_id,
        "metadata":{"app":"app"},"amount_paid":5000,"currency":"usd","period_start":int(timezone.now().timestamp()),
        "lines":{"data":[{"price":{"id":"price-annual"}}]}}}}
    assert process_stripe_event(event)=="processed"
    sub.refresh_from_db()
    assert sub.status=="canceled" and not ContractPayment.objects.filter(stripe_invoice_id="in-late").exists()
    assert not TokenEntry.objects.filter(account__user=u,kind="grant").exists()


def test_transition_guard_ends_subscription_when_schedule_is_missing(monkeypatch,billing_config):
    u=user(6); end=timezone.now()+timedelta(days=2)
    contract,_=create_contract(u,initial_term_end=end,schedule_status="failed")
    sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id); sub.current_period_end=end; sub.save(update_fields=("current_period_end",))
    calls=[]
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Subscription=SimpleNamespace(modify=lambda *a,**kw:calls.append((a,kw)))))
    assert run_transition_guard()==1
    contract.refresh_from_db(); sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id)
    assert calls[0][1]=={"cancel_at_period_end":True}
    assert contract.needs_review and contract.review_reason=="transition_schedule_missing"
    assert contract.status=="cancellation_scheduled" and contract.cancel_effective_at==end and sub.cancel_at_period_end


def test_contract_summary_only_shows_a_scheduled_recurring_charge(billing_config):
    u=user(10); contract,_=create_contract(u,schedule_status="created")
    summary=contract_summary(contract)
    assert summary["next_charge"]=={"amount_cents":500,"currency":"usd","date":contract.initial_term_end.isoformat(),"token_allowance":100}
    contract.schedule_status="failed"; contract.save(update_fields=("schedule_status",))
    assert contract_summary(contract)["next_charge"] is None
    contract.schedule_status="created"; contract.status="cancellation_scheduled"; contract.cancel_effective_at=contract.initial_term_end
    contract.save(update_fields=("schedule_status","status","cancel_effective_at"))
    assert contract_summary(contract)["next_charge"] is None


def test_full_policy_refunds_a_fully_used_token_package_in_full(monkeypatch,billing_config,django_capture_on_commit_callbacks):
    u=user(11); now=timezone.now()
    purchase=Purchase.objects.create(user=u,kind="token_package",plan_key="one_time",stripe_object_id=f"pi-package-{u.pk}",
        stripe_payment_intent_id="pi-token-package",amount_paid_cents=1200,currency="usd",billing_country="FR",purchased_at=now,
        grant_references=["stripe:checkout:cs-token"],tokens_granted=500)
    contract=Contract.objects.create(user=u,app="app",kind="token_package",plan_key="one_time",purchase=purchase,
        stripe_object_id=purchase.stripe_object_id,withdrawal_policy="full",consumer_status="consumer",billing_country="FR",
        started_at=now,info_delivered_at=now,phase="none",schedule_status="not_needed")
    payment=ContractPayment.objects.create(contract=contract,stripe_payment_intent_id="pi-token-package",amount_paid_cents=1200,currency="usd")
    account=TokenAccount.objects.create(user=u)
    TokenEntry.objects.create(account=account,kind="purchase",amount=500,remaining=0,reference="stripe:checkout:cs-token",app="app")
    calls=[]
    stripe=SimpleNamespace(Refund=SimpleNamespace(create=lambda **kw:(calls.append(kw) or {"id":"re-token","status":"succeeded"})),
        PaymentIntent=SimpleNamespace(retrieve=lambda pi:{"latest_charge":"ch-token"}),Charge=SimpleNamespace(retrieve=lambda ch:{"amount_refunded":0}))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
    payment.refresh_from_db()
    assert calls[0]["amount"]==1200 and payment.refunded_cents==1200
    assert TokenEntry.objects.filter(account=account,kind="expiry").count()==0


def test_full_withdrawal_preview_has_only_the_full_policy_fields(billing_config):
    u=user(12); contract,purchase=create_contract(u)
    ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-preview",amount_paid_cents=1000,refunded_cents=200,currency="usd")
    from django.test import RequestFactory
    from django_core_micha.billing.views import withdrawal_preview
    request=RequestFactory().get("/withdrawal/"); request.user=u
    response=withdrawal_preview(request,purchase.pk); body=__import__("json").loads(response.content)
    assert set(body)=={"policy","eligible","reason","deadline","amount_paid_cents","refunded_cents","refund_cents","currency"}
    assert body["policy"]=="full" and body["amount_paid_cents"]==1000 and body["refunded_cents"]==200 and body["refund_cents"]==800



def test_invoice_payment_intent_reads_old_field_payments_list_and_invoice_payment_api(monkeypatch):
    from django_core_micha.billing import stripe_service
    assert stripe_service._invoice_payment_intent({"id":"in_1","payment_intent":"pi_old"})=="pi_old"
    assert stripe_service._invoice_payment_intent({"id":"in_2","payments":{"data":[{"status":"paid","payment":{"type":"payment_intent","payment_intent":"pi_listed"}}]}})=="pi_listed"
    calls=[]
    fake=SimpleNamespace(InvoicePayment=SimpleNamespace(list=lambda **kw:(calls.append(kw) or SimpleNamespace(data=[{"status":"open","payment":{"payment_intent":"pi_open"}},{"status":"paid","payment":{"payment_intent":"pi_new"}}]))))
    monkeypatch.setattr(stripe_service,"_stripe",lambda:fake)
    assert stripe_service._invoice_payment_intent({"id":"in_3"})=="pi_new"
    assert calls==[{"invoice":"in_3","limit":10}]


def test_subscription_end_prefers_ended_at_over_the_cancellation_request_time(billing_config):
    from django_core_micha.billing.stripe_service import _upsert_subscription
    u=get_user_model().objects.create_user(username=f"end-{uuid4().hex[:8]}",email="end@example.test",password="x")
    requested=int(datetime(2026,11,4,tzinfo=dt_timezone.utc).timestamp()); ended=int(datetime(2027,11,4,tzinfo=dt_timezone.utc).timestamp())
    sub=_upsert_subscription({"id":"sub_end","status":"canceled","canceled_at":requested,"ended_at":ended,"metadata":{"app":"app","plan_key":"","user_id":str(u.pk)},
        "current_period_start":requested,"current_period_end":ended},user=u)
    assert sub.ended_at==datetime(2027,11,4,tzinfo=dt_timezone.utc)


def test_full_withdrawal_refunds_the_initial_payment_and_leaves_a_separate_purchase(monkeypatch,billing_config,django_capture_on_commit_callbacks):
    u=user(50); contract,purchase=create_contract(u)
    ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-initial",stripe_payment_intent_id="pi-initial",amount_paid_cents=5405,currency="usd",billing_reason="initial")
    other_purchase=Purchase.objects.create(user=u,kind="token_package",plan_key="one_time",stripe_object_id="pi-tokens",stripe_payment_intent_id="pi-tokens",
        amount_paid_cents=1000,currency="usd",billing_country="DE",purchased_at=timezone.now(),grant_references=["stripe:checkout:tokens"],tokens_granted=100)
    other=Contract.objects.create(user=u,app="app",kind="token_package",plan_key="one_time",purchase=other_purchase,stripe_object_id="pi-tokens",
        terms_version="2026-11-04",seller="Example Seller Ltd",withdrawal_policy="full",consumer_status="consumer",billing_country="DE",
        started_at=timezone.now(),info_delivered_at=timezone.now(),phase="none",status="active")
    ContractPayment.objects.create(contract=other,stripe_payment_intent_id="pi-tokens",amount_paid_cents=1000,currency="usd",billing_reason="initial")
    account=TokenAccount.objects.create(user=u)
    TokenEntry.objects.create(account=account,kind="purchase",amount=100,remaining=100,reference="stripe:checkout:tokens",app="app")
    calls=[]
    stripe=SimpleNamespace(Subscription=SimpleNamespace(cancel=lambda *a,**k:{"id":"sub"}),
        Refund=SimpleNamespace(create=lambda **kw:(calls.append(kw) or {"id":"re-initial","status":"succeeded"})),
        PaymentIntent=SimpleNamespace(retrieve=lambda pi:{"latest_charge":"ch-initial"}),Charge=SimpleNamespace(retrieve=lambda ch:{"amount_refunded":0}))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    with django_capture_on_commit_callbacks(execute=True): apply_withdrawal(declaration)
    other.refresh_from_db(); other_purchase.refresh_from_db()
    assert [(c["payment_intent"],c["amount"]) for c in calls]==[("pi-initial",5405)]
    assert other.status=="active" and other_purchase.status=="active"
    assert token_balance(u)==100


def test_definitively_failed_refund_is_retried_with_a_fresh_row_and_key(monkeypatch,billing_config):
    from django_core_micha.billing.contracts import process_refund,retry_refund
    u=user(51); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-failed",stripe_payment_intent_id="pi-failed",amount_paid_cents=900,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    row=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=900,currency="usd",
        idempotency_key="dcm-refund:app:failed-1",due_by=declaration.received_at+timedelta(days=14))
    results=iter([{"id":"re-f1","status":"failed"},{"id":"re-f2","status":"succeeded"}]); calls=[]
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(Refund=SimpleNamespace(create=lambda **kw:(calls.append(kw) or next(results)))))
    assert process_refund(row.pk).status=="failed"
    fresh=retry_refund(row.pk)
    row.refresh_from_db()
    assert row.status=="cancelled" and fresh.pk!=row.pk and fresh.status=="succeeded"
    assert calls[0]["idempotency_key"]!=calls[1]["idempotency_key"] and calls[1]["amount"]==900


def test_operator_extraordinary_termination_ends_now_and_refunds_prepaid_time(monkeypatch,billing_config,django_capture_on_commit_callbacks):
    from django_core_micha.billing.contracts import apply_extraordinary_termination
    u=user(52); contract,_=create_contract(u)
    ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-x",stripe_payment_intent_id="pi-x",amount_paid_cents=5000,currency="usd")
    calls=[]
    stripe=SimpleNamespace(Subscription=SimpleNamespace(cancel=lambda *a,**k:calls.append(("cancel",a)) or {"id":"sub"}),
        Refund=SimpleNamespace(create=lambda **kw:(calls.append(("refund",kw["amount"])) or {"id":"re-x","status":"succeeded"})),
        PaymentIntent=SimpleNamespace(retrieve=lambda pi:{"latest_charge":"ch-x"}),Charge=SimpleNamespace(retrieve=lambda ch:{"amount_refunded":0}))
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:stripe)
    declaration=Declaration.objects.create(app="app",kind="cancellation",channel="public",cancellation_type="extraordinary",reason="Service unusable",
        name="x",email=u.email,user=u,contract=contract,status="needs_review")
    received=declaration.received_at
    with django_capture_on_commit_callbacks(execute=True):
        apply_extraordinary_termination(declaration,timezone.now().date()-timedelta(days=1),2500)
    declaration.refresh_from_db(); contract.refresh_from_db()
    assert declaration.status=="effective" and contract.status=="ended" and declaration.received_at==received
    assert ("refund",2500) in calls and any(c[0]=="cancel" for c in calls)


def test_me_keeps_the_name_of_a_plan_that_is_no_longer_offered(billing_config):
    from django_core_micha.billing.views import _plan_names
    assert _plan_names()["one_time"]=="Tokens"
