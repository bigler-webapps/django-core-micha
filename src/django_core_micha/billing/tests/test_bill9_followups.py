"""DCM-BILL-9: the DCM-BILL-8 closing-review follow-ups (refund failure code, scheduled cancellation, provider refunds,
withdrawal resumption, public-intake timing) and the test gaps that review listed."""
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from django_core_micha.billing import declarations
from django_core_micha.billing.contracts import apply_due_cancellations, resume_withdrawals, retry_refund, run_transition_guard
from django_core_micha.billing.models import (Contract, ContractPayment, Declaration, Purchase, Refund, Subscription, TokenAccount, TokenEntry)
from django_core_micha.billing.services import token_balance
from django_core_micha.billing.stripe_service import process_stripe_event

pytestmark=pytest.mark.django_db

YAML="""app: app
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
  - key: legacy
    kind: subscription
    name: Legacy plan
    amount_cents: 3000
    interval: year
    token_allowance: 600
    tax_code: txcd_10000000
    available: false
"""

NO_CONTRACT_YAML="""app: app
currency: usd
token_rates: {}
plans:
  - key: annual
    kind: subscription
    name: Annual
    amount_cents: 5000
    interval: year
    token_allowance: 1200
    tax_code: txcd_10000000
"""


def write_config(request,monkeypatch,text,confirm_url=True):
    path=Path.cwd()/f".billing-test-{uuid4().hex}.yaml"; path.write_text(text,encoding="utf-8")
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_FILE",str(path),raising=False)
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_APP","app",raising=False)
    if confirm_url: monkeypatch.setattr("django.conf.settings.DCM_BILLING_DECLARATION_CONFIRM_URL","https://front.example/confirm?token={token}",raising=False)
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path


@pytest.fixture
def billing_config(request,monkeypatch):
    return write_config(request,monkeypatch,YAML)


@pytest.fixture
def no_contract_config(request,monkeypatch):
    return write_config(request,monkeypatch,NO_CONTRACT_YAML,confirm_url=False)


def user(i,email=None):
    return get_user_model().objects.create_user(username=f"bill9-{i}",email=email or f"bill9-{i}@example.com",password="x")


def create_contract(u,*,app="app",status="active",**changes):
    now=timezone.now()
    purchase=Purchase.objects.create(user=u,kind="subscription",plan_key="annual",stripe_object_id=f"sub-{app}-{u.pk}",
        stripe_payment_intent_id=f"pi-{app}-{u.pk}",amount_paid_cents=5000,currency="usd",billing_country="DE",
        purchased_at=now,term_start=now,term_end=now+timedelta(days=365),grant_references=[],tokens_granted=1200)
    values={"user":u,"app":app,"kind":"subscription","plan_key":"annual","purchase":purchase,
        "stripe_object_id":purchase.stripe_object_id,"terms_version":"2026-11-04","seller":"Example Seller Ltd",
        "withdrawal_policy":"full","consumer_status":"consumer","billing_country":"DE","started_at":now,
        "info_delivered_at":now,"initial_term_end":now+timedelta(days=365),"phase":"initial",
        "status":status,"continuation_plan_key":"monthly","continuation_stripe_price_id":"price-monthly",
        "continuation_amount_cents":500,"continuation_token_allowance":100,"continuation_interval":"month",
        "schedule_status":"pending"}
    values.update(changes)
    contract=Contract.objects.create(**values)
    Subscription.objects.create(user=u,plan_key="annual",stripe_subscription_id=purchase.stripe_object_id,
        status="active",current_period_start=now,current_period_end=now+timedelta(days=365))
    return contract,purchase


def stripe_with(monkeypatch,**parts):
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:SimpleNamespace(**parts))


def refund_event(kind,row,*,status,amount=None):
    return {"type":kind,"data":{"object":{"id":"re-event","status":status,"amount":amount or row.amount_cents,"currency":"usd",
        "payment_intent":row.payment.stripe_payment_intent_id,
        "metadata":{"app":"app","contract_id":str(row.contract_id),"dcm_refund":row.idempotency_key}}}}


# --- decision 1: one error code for a provider-failed refund -------------------------------------------------------

@pytest.mark.parametrize("legacy",[False,True])
def test_provider_failed_refund_gets_a_fresh_row_and_key_on_retry(monkeypatch,billing_config,legacy):
    u=user(1); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-f",stripe_payment_intent_id="pi-f",amount_paid_cents=900,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    row=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=750,currency="usd",status="requested",
        idempotency_key="dcm-refund:app:failed-hook",due_by=declaration.received_at+timedelta(days=14))
    row=Refund.objects.select_related("payment").get(pk=row.pk)
    if legacy:
        # a row written by 3.21.0, which stored the event name
        row.status="failed"; row.last_error_code="refund.failed"; row.save(update_fields=("status","last_error_code"))
    else:
        assert process_stripe_event(refund_event("refund.failed",row,status="failed"))=="processed"
        row.refresh_from_db()
        assert row.status=="failed" and row.last_error_code=="refund_failed"
    calls=[]
    stripe_with(monkeypatch,Refund=SimpleNamespace(create=lambda **kw:(calls.append(kw) or {"id":"re-new","status":"succeeded"})))
    fresh=retry_refund(row.pk)
    row.refresh_from_db(); payment.refresh_from_db()
    assert row.status=="cancelled" and fresh.pk!=row.pk and fresh.status=="succeeded" and fresh.amount_cents==750
    assert len(calls)==1 and calls[0]["idempotency_key"]!=row.idempotency_key and calls[0]["amount"]==750
    assert payment.refunded_cents==750


def test_a_failed_refund_event_replayed_after_the_retry_changes_nothing(monkeypatch,billing_config):
    u=user(2); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-r",stripe_payment_intent_id="pi-r",amount_paid_cents=900,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    row=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=900,currency="usd",status="requested",
        idempotency_key="dcm-refund:app:replay",due_by=declaration.received_at+timedelta(days=14))
    row=Refund.objects.select_related("payment").get(pk=row.pk)
    event=refund_event("refund.failed",row,status="failed")
    process_stripe_event(event)
    stripe_with(monkeypatch,Refund=SimpleNamespace(create=lambda **kw:{"id":"re-ok","status":"succeeded"}))
    retry_refund(row.pk)
    process_stripe_event(event)
    row.refresh_from_db(); payment.refresh_from_db()
    assert row.status=="cancelled" and payment.refunded_cents==900
    assert Refund.objects.filter(payment=payment).exclude(status="cancelled").count()==1


# --- decision 2: the batch job ends the local subscription with the scheduled cancellation --------------------------

def scheduled_cancellation(u):
    end=timezone.now()-timedelta(hours=1)
    contract,_=create_contract(u,status="cancellation_scheduled",cancel_effective_at=end,phase="continuation",schedule_status="not_needed")
    sub=Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id)
    sub.current_period_end=end; sub.cancel_at_period_end=True; sub.save(update_fields=("current_period_end","cancel_at_period_end"))
    declaration=Declaration.objects.create(app="app",kind="cancellation",channel="account",user=u,contract=contract,
        cancellation_type="ordinary",status="scheduled",effective_at=end)
    return contract,sub,declaration,end


def deletion_event(contract,u,end):
    stamp=int(end.timestamp())
    return {"type":"customer.subscription.deleted","data":{"object":{"id":contract.stripe_object_id,"status":"canceled","ended_at":stamp,
        "canceled_at":stamp-86400,"current_period_start":stamp-86400*30,"current_period_end":stamp,"cancel_at_period_end":False,
        "metadata":{"app":"app","user_id":str(u.pk),"plan_key":"annual"},"items":{"data":[]}}}}


@pytest.mark.parametrize("batch_first",[True,False])
def test_scheduled_cancellation_ends_locally_in_either_event_order(billing_config,batch_first):
    u=user(3); contract,sub,declaration,end=scheduled_cancellation(u)
    if batch_first:
        assert apply_due_cancellations()==1
        sub.refresh_from_db()
        assert sub.status=="canceled" and sub.ended_at==end and sub.cancel_at_period_end is False
        process_stripe_event(deletion_event(contract,u,end))  # the late webhook is idempotent
    else:
        process_stripe_event(deletion_event(contract,u,end))
        assert apply_due_cancellations()==0
    sub.refresh_from_db(); contract.refresh_from_db(); declaration.refresh_from_db()
    assert sub.status=="canceled" and contract.status=="ended" and declaration.status=="effective"
    assert apply_due_cancellations()==0 and token_balance(u)==0


def test_a_cancellation_whose_period_has_not_ended_leaves_the_subscription_active(billing_config):
    u=user(4); contract,sub,declaration,end=scheduled_cancellation(u)
    sub.current_period_end=timezone.now()+timedelta(days=10); sub.save(update_fields=("current_period_end",))
    contract.cancel_effective_at=sub.current_period_end; contract.save(update_fields=("cancel_effective_at",))
    declaration.effective_at=sub.current_period_end; declaration.save(update_fields=("effective_at",))
    assert apply_due_cancellations()==0
    sub.refresh_from_db()
    assert sub.status=="active"


# --- decision 3: provider-originated refunds are reconciled ----------------------------------------------------------

def charge_event(payment_intent,amount_refunded):
    return {"type":"charge.refunded","data":{"object":{"id":"ch-prov","payment_intent":payment_intent,"amount_refunded":amount_refunded}}}


def test_a_partial_refund_made_in_stripe_reaches_refunded_cents_and_the_preview(billing_config):
    u=user(5); contract,purchase=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-prov",stripe_payment_intent_id="pi-prov",amount_paid_cents=1000,currency="usd")
    for _ in range(2): assert process_stripe_event(charge_event("pi-prov",300))=="processed"  # the replay changes nothing
    payment.refresh_from_db(); assert payment.refunded_cents==300
    request=RequestFactory().get("/withdrawal/"); request.user=u
    from django_core_micha.billing.views import withdrawal_preview
    body=json.loads(withdrawal_preview(request,purchase.pk).content)
    assert body["refunded_cents"]==300 and body["refund_cents"]==700
    process_stripe_event(charge_event("pi-prov",100))  # an older, smaller total never lowers it
    payment.refresh_from_db(); assert payment.refunded_cents==300


def test_a_provider_refund_of_another_app_or_unknown_intent_is_ignored(billing_config):
    u=user(6); other,_=create_contract(u,app="other")
    foreign=ContractPayment.objects.create(contract=other,stripe_invoice_id="in-other",stripe_payment_intent_id="pi-other",amount_paid_cents=1000,currency="usd")
    process_stripe_event(charge_event("pi-other",400)); process_stripe_event(charge_event("pi-unknown",400))
    foreign.refresh_from_db(); assert foreign.refunded_cents==0


def test_our_own_refund_and_a_provider_refund_are_counted_once_in_any_order(monkeypatch,billing_config):
    u=user(7); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-mix",stripe_payment_intent_id="pi-mix",amount_paid_cents=1000,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    row=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=500,currency="usd",status="requested",
        stripe_refund_id="re-event",idempotency_key="dcm-refund:app:mix",due_by=declaration.received_at+timedelta(days=14))
    row=Refund.objects.select_related("payment").get(pk=row.pk)
    stripe_with(monkeypatch,Refund=SimpleNamespace(list=lambda **kw:SimpleNamespace(data=[])))
    process_stripe_event(charge_event("pi-mix",800))  # 300 made in Stripe + our 500, which is still open here
    payment.refresh_from_db(); assert payment.refunded_cents==300
    process_stripe_event(refund_event("refund.updated",row,status="succeeded"))
    payment.refresh_from_db(); assert payment.refunded_cents==800
    process_stripe_event(charge_event("pi-mix",800)); process_stripe_event(refund_event("refund.updated",row,status="succeeded"))
    payment.refresh_from_db(); assert payment.refunded_cents==800


def test_a_refund_stripe_has_not_accepted_yet_is_not_subtracted_from_the_provider_total(monkeypatch,billing_config):
    stripe_with(monkeypatch,Refund=SimpleNamespace(list=lambda **kw:SimpleNamespace(data=[])))
    u=user(17); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-unsent",stripe_payment_intent_id="pi-unsent",
        amount_paid_cents=1000,refunded_cents=500,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=400,currency="usd",status="pending",
        idempotency_key="dcm-refund:app:unsent",due_by=declaration.received_at+timedelta(days=14))  # no Stripe id: never accepted
    process_stripe_event(charge_event("pi-unsent",800))  # 500 ours (succeeded) + 300 made in Stripe
    payment.refresh_from_db(); assert payment.refunded_cents==800


# --- decision 4: withdrawal resumption never replays a failed refund row ---------------------------------------------

def test_resuming_a_withdrawal_retries_a_failed_refund_with_a_fresh_key(monkeypatch,billing_config):
    u=user(8); contract,purchase=create_contract(u,status="withdrawn")
    purchase.status="withdrawn"; purchase.save(update_fields=("status",))
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-res",stripe_payment_intent_id="pi-res",amount_paid_cents=900,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract,status="accepted")
    failed=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=900,currency="usd",status="failed",
        last_error_code="refund_failed",idempotency_key="dcm-refund:app:failed-before",due_by=declaration.received_at+timedelta(days=14))
    calls=[]
    stripe_with(monkeypatch,Subscription=SimpleNamespace(cancel=lambda *a,**k:{"id":"sub"}),
        Refund=SimpleNamespace(create=lambda **kw:(calls.append(kw) or {"id":"re-res","status":"succeeded"})),
        PaymentIntent=SimpleNamespace(retrieve=lambda pi:{"latest_charge":"ch-res"}),Charge=SimpleNamespace(retrieve=lambda ch:{"amount_refunded":0}))
    assert resume_withdrawals()==1
    failed.refresh_from_db(); payment.refresh_from_db(); declaration.refresh_from_db()
    assert failed.status=="cancelled"
    assert len(calls)==1 and calls[0]["idempotency_key"]!="dcm-refund:app:failed-before" and calls[0]["amount"]==900
    assert payment.refunded_cents==900 and declaration.status=="effective"


def test_the_batch_command_retries_a_failed_refund_with_a_fresh_row(monkeypatch,billing_config):
    u=user(9); contract,_=create_contract(u)
    payment=ContractPayment.objects.create(contract=contract,stripe_invoice_id="in-cmd",stripe_payment_intent_id="pi-cmd",amount_paid_cents=700,currency="usd")
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="account",user=u,contract=contract)
    failed=Refund.objects.create(contract=contract,payment=payment,declaration=declaration,amount_cents=700,currency="usd",status="failed",
        last_error_code="refund_failed",idempotency_key="dcm-refund:app:cmd-failed",due_by=declaration.received_at+timedelta(days=14))
    calls=[]
    stripe_with(monkeypatch,Refund=SimpleNamespace(create=lambda **kw:(calls.append(kw) or {"id":"re-cmd","status":"succeeded"})))
    call_command("billing_process_declarations")
    failed.refresh_from_db(); payment.refresh_from_db()
    assert failed.status=="cancelled" and len(calls)==1 and calls[0]["idempotency_key"]!=failed.idempotency_key
    assert payment.refunded_cents==700


# --- decision 5: matched and unmatched public submissions do the same work before the response ----------------------

def public_request(factory,email,ip):
    body={"kind":"cancellation","name":"Declarant","email":email,"contract_hint":"","cancellation_type":"ordinary",
        "requested_end_date":"","reason":"","message":"Please cancel."}
    request=factory.post("/public/declarations/",data=json.dumps(body),content_type="application/json",REMOTE_ADDR=ip)
    from django.contrib.auth.models import AnonymousUser
    request.user=AnonymousUser()
    return request


def test_public_intake_defers_every_match_dependent_step_until_after_the_response(monkeypatch,billing_config,django_capture_on_commit_callbacks):
    from django.core.cache import cache
    from django_core_micha.billing.views import public_declarations
    cache.clear()
    owner=user(10,"payer-timing@example.com"); contract,_=create_contract(owner)
    deferred=[]
    monkeypatch.setattr(declarations,"_in_background",lambda function,*args:deferred.append((function,args)))
    factory=RequestFactory(); mail.outbox.clear(); counts={}
    with django_capture_on_commit_callbacks(execute=True):
        for label,email,ip in (("matched",owner.email,"192.0.2.31"),("unmatched","nobody-timing@example.com","192.0.2.32")):
            with CaptureQueriesContext(connection) as queries:
                response=public_declarations(public_request(factory,email,ip))
            assert response.status_code==201
            counts[label]=len(queries)
            # the contract is not even looked up before the response, so no query can carry a match signal
            assert not [q["sql"] for q in queries.captured_queries if 'FROM "billing_contract"' in q["sql"]]
    assert counts["matched"]==counts["unmatched"]
    # before the deferred work runs: no mail, no match, no token, the same stored state for both
    assert mail.outbox==[]
    rows=list(Declaration.objects.filter(channel="public").order_by("pk"))
    assert [r.status for r in rows]==["received","received"]
    assert all(r.contract_id is None and r.user_id is None and not r.confirm_token_hash for r in rows)
    assert [function.__name__ for function,_ in deferred]==["finish_public_declaration"]*2
    for function,args in deferred: function(*args)
    matched,unmatched=Declaration.objects.get(pk=rows[0].pk),Declaration.objects.get(pk=rows[1].pk)
    assert matched.status=="awaiting_confirmation" and matched.contract_id==contract.pk and matched.confirm_token_hash
    assert unmatched.status=="needs_review" and unmatched.contract_id is None
    assert len(mail.outbox)==3  # a receipt for each, plus the owner's confirmation


def test_finishing_a_public_declaration_runs_once_and_a_stuck_one_is_recovered(billing_config):
    owner=user(11,"payer-stuck@example.com"); contract,_=create_contract(owner)
    row=Declaration.objects.create(app="app",kind="cancellation",channel="public",name="Stuck",email=owner.email,cancellation_type="ordinary",status="received")
    mail.outbox.clear()
    assert declarations.finish_public_declaration(row.pk) is True
    assert declarations.finish_public_declaration(row.pk) is False  # not received any more
    assert len(mail.outbox)==2
    fresh=Declaration.objects.create(app="app",kind="cancellation",channel="public",name="Fresh",email="fresh-stuck@example.com",cancellation_type="ordinary",status="received")
    stuck=Declaration.objects.create(app="app",kind="cancellation",channel="public",name="Old",email="old-stuck@example.com",cancellation_type="ordinary",status="received")
    owner2=user(18,"payer-stuck2@example.com"); contract2,_=create_contract(owner2)
    stuck_matched=Declaration.objects.create(app="app",kind="cancellation",channel="public",name="Old owner",email=owner2.email,cancellation_type="ordinary",status="received")
    Declaration.objects.filter(pk__in=(stuck.pk,stuck_matched.pk)).update(received_at=timezone.now()-timedelta(minutes=30))
    mail.outbox.clear()
    declarations.retry_declaration_emails()
    fresh.refresh_from_db(); stuck.refresh_from_db(); stuck_matched.refresh_from_db()
    assert stuck.status=="needs_review"
    # a stuck declaration of a contract owner is matched, gets its token and the owner's confirmation mail, not just a status
    assert stuck_matched.status=="awaiting_confirmation" and stuck_matched.contract_id==contract2.pk and stuck_matched.confirm_token_hash
    assert [m.to for m in mail.outbox if "confirm" in m.subject.lower()]==[[owner2.email]]
    assert fresh.status=="received"  # still inside the five minutes the request thread has


# --- carried test gaps -------------------------------------------------------------------------------------------------

def test_me_endpoint_names_a_plan_that_is_no_longer_offered(billing_config):
    from django_core_micha.billing.views import me
    u=user(12); now=timezone.now()
    Subscription.objects.create(user=u,plan_key="legacy",stripe_subscription_id="sub-legacy",status="active",
        current_period_start=now,current_period_end=now+timedelta(days=100))
    request=RequestFactory().get("/me/"); request.user=u
    body=json.loads(me(request).content)
    assert body["subscription"]["plan_key"]=="legacy" and body["subscription"]["plan_name"]=="Legacy plan"
    assert "legacy" not in {p["key"] for p in body["plans"]}


def test_every_contract_endpoint_is_a_404_for_an_app_without_contract_config(no_contract_config):
    from django_core_micha.billing import operator, views
    u=user(13); u.is_staff=True; u.is_superuser=True; u.save()
    factory=RequestFactory()
    def get(path):
        request=factory.get(path); request.user=u; request.session={"auth_level":"strong"}; return request
    def post(path):
        request=factory.post(path,data="{}",content_type="application/json"); request.user=u; return request
    responses={
        "public csrf":views.public_declarations_csrf(get("/public/declarations/csrf/")),
        "public submit":views.public_declarations(post("/public/declarations/")),
        "public confirm":views.public_declarations_confirm(post("/public/declarations/confirm/")),
        "contracts":views.contracts_list(get("/contracts/")),
        "cancel":views.contract_cancel(post("/contracts/x/cancel/"),"x"),
        "withdraw":views.contract_withdraw(post("/contracts/x/withdraw/"),"x"),
    }
    with override_settings(SECURITY_DEFAULT_LEVEL="basic",SECURITY_LEVELS=("basic","strong"),INVITE_MIN_ROLE_LEVEL=2,AUTH_POLICY_WRITE_MIN_ROLE_LEVEL=3,
            INVITE_ADMIN_ROLES=(),ROLE_DEFINITIONS={"user":{"level":1}},DEFAULT_ROLE_CODE="user"):
        responses["operator declarations"]=operator.declarations(get("/operator/declarations/"))
    assert {name:response.status_code for name,response in responses.items()}=={name:404 for name in responses}


def test_an_extraordinary_termination_with_a_future_date_is_scheduled_not_applied(monkeypatch,billing_config):
    u=user(14); contract,_=create_contract(u)
    calls=[]
    stripe_with(monkeypatch,Subscription=SimpleNamespace(modify=lambda *a,**kw:calls.append(("modify",a,kw)),cancel=lambda *a,**kw:calls.append(("cancel",a))),
        SubscriptionSchedule=SimpleNamespace(release=lambda *a,**kw:calls.append(("release",a))))
    declaration=Declaration.objects.create(app="app",kind="cancellation",channel="public",cancellation_type="extraordinary",reason="Service unusable",
        name="x",email=u.email,user=u,contract=contract,status="needs_review")
    future=timezone.now().date()+timedelta(days=20)
    # through the operator command, as an operator resolves a queued declaration
    call_command("billing_resolve_declaration",declaration.reference,"--accept","--effective",future.isoformat(),"--note","ok")
    declaration.refresh_from_db(); contract.refresh_from_db()
    assert [c[0] for c in calls]==["modify"] and calls[0][2]["proration_behavior"]=="none" and "cancel_at" in calls[0][2]
    assert declaration.status=="scheduled" and contract.status=="cancellation_scheduled" and contract.cancel_effective_at.date()==future
    assert Subscription.objects.get(stripe_subscription_id=contract.stripe_object_id).status=="active"


def test_the_transition_guard_only_touches_contracts_of_its_own_app(monkeypatch,billing_config):
    u=user(15); end=timezone.now()+timedelta(days=2)
    mine,_=create_contract(u,initial_term_end=end,schedule_status="failed")
    v=user(16); other,_=create_contract(v,app="other",initial_term_end=end,schedule_status="failed")
    calls=[]
    stripe_with(monkeypatch,Subscription=SimpleNamespace(modify=lambda sid,**kw:calls.append(sid)))
    assert run_transition_guard()==1
    mine.refresh_from_db(); other.refresh_from_db()
    assert calls==[mine.stripe_object_id]
    assert mine.review_reason=="transition_schedule_missing" and other.review_reason=="" and other.status=="active"
