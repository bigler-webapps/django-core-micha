import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.cache import cache
from django.http import Http404
from django.test import RequestFactory
from django.utils import timezone

from django_core_micha.billing.declarations import (DeclarationInputError,check_public_throttles,
    confirm_public_declaration,retry_declaration_emails,validate_declaration)
from django_core_micha.billing.models import Contract,Declaration,Purchase,Subscription
from django_core_micha.billing.views import (contract_cancel,contracts_list,public_declarations,
    public_declarations_confirm,withdrawal_list,withdrawal_preview)

pytestmark=pytest.mark.django_db


YAML="""app: app
currency: usd
token_rates: {}
contract:
  terms_version: '2026-11-04'
  seller: 'Example Seller Ltd'
  withdrawal_refund: full
  public_declarations: true
plans:
  - key: annual
    kind: subscription
    name: Annual
    amount_cents: 5000
    interval: year
    token_allowance: 1200
    tax_code: txcd_10000000
  - key: tokens
    kind: token_package
    name: Tokens
    amount_cents: 1000
    tokens: 100
    tax_code: txcd_10000000
"""


@pytest.fixture
def declaration_config(request,monkeypatch):
    path=Path.cwd()/f".billing-test-{uuid4().hex}.yaml"; path.write_text(YAML,encoding="utf-8")
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_FILE",str(path),raising=False)
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_APP","app",raising=False)
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_DECLARATION_CONFIRM_URL","https://front.example/confirm?token={token}",raising=False)
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path


def make_user(i,email=None):
    return get_user_model().objects.create_user(username=f"declaration-{i}",email=email or f"declaration-{i}@example.com",password="x")


def make_contract(owner,*,app="app"):
    now=timezone.now()
    return Contract.objects.create(user=owner,app=app,kind="subscription",plan_key="annual",stripe_object_id=f"sub-{owner.pk}-{app}",
        reference=f"contract-{owner.pk}-{app}",terms_version="2026-11-04",seller="Example Seller Ltd",withdrawal_policy="full",
        consumer_status="consumer",billing_country="DE",started_at=now,info_delivered_at=now,
        initial_term_end=now+timedelta(days=365),phase="initial",status="active")


def payload(email):
    return {"kind":"cancellation","name":"Test Declarant","email":email,"contract_hint":"","cancellation_type":"ordinary",
        "requested_end_date":"","reason":"ignored reason","message":"Please cancel."}


def request_for(factory,body,*,path="/public/declarations/",ip="192.0.2.10"):
    request=factory.post(path,data=json.dumps(body),content_type="application/json",REMOTE_ADDR=ip)
    request.user=AnonymousUser()
    return request


def test_public_intake_responds_opaquely_and_sends_no_contract_data(monkeypatch,declaration_config,django_capture_on_commit_callbacks):
    owner=make_user(1,"payer@example.com"); contract=make_contract(owner)
    factory=RequestFactory(); mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        matching=public_declarations(request_for(factory,payload(owner.email),ip="192.0.2.1"))
        unmatched=public_declarations(request_for(factory,payload("nobody@example.com"),ip="192.0.2.2"))
    left=json.loads(matching.content); right=json.loads(unmatched.content)
    assert matching.status_code==unmatched.status_code==201
    assert set(left)==set(right)=={"reference","kind","received_at","submitted","status"}
    assert left["status"]==right["status"]=="received"
    assert contract.reference not in matching.content.decode()
    assert len(mail.outbox)==3  # receipt for each submission, plus owner confirmation
    assert all(contract.reference not in message.body for message in mail.outbox)
    matched=Declaration.objects.get(reference=left["reference"])
    unmatched_row=Declaration.objects.get(reference=right["reference"])
    assert matched.status=="awaiting_confirmation" and matched.contract_id==contract.pk
    assert unmatched_row.status=="needs_review" and unmatched_row.contract_id is None


def test_public_submission_validates_bounds_and_ordinary_reason_is_ignored(declaration_config):
    body=payload("valid@example.com")
    values=validate_declaration(body)
    assert values["reason"]==""
    body["message"]="x"*2001
    with pytest.raises(DeclarationInputError): validate_declaration(body)
    body=payload("valid@example.com"); body["cancellation_type"]="extraordinary"; body["reason"]=" "
    with pytest.raises(DeclarationInputError): validate_declaration(body)


def test_public_throttles_by_ip_and_submitted_email():
    cache.clear(); factory=RequestFactory()
    request=factory.post("/",REMOTE_ADDR="192.0.2.9")
    assert all(check_public_throttles(request) for _ in range(10))
    assert not check_public_throttles(request)
    cache.clear()
    assert all(check_public_throttles(factory.post("/",REMOTE_ADDR=f"192.0.2.{i}"),"same@example.com") for i in range(3))
    assert not check_public_throttles(factory.post("/",REMOTE_ADDR="192.0.2.20"),"SAME@example.com")


def test_public_endpoint_enforces_csrf_and_confirmation_is_signed_single_use(monkeypatch,declaration_config,django_capture_on_commit_callbacks):
    owner=make_user(2,"confirm@example.com"); contract=make_contract(owner)
    end=contract.initial_term_end
    Subscription.objects.create(user=owner,plan_key="annual",stripe_subscription_id=contract.stripe_object_id,status="active",
        current_period_start=contract.started_at,current_period_end=end)
    request=request_for(RequestFactory(),payload(owner.email))
    request._dont_enforce_csrf_checks=False
    denied=public_declarations(request)
    assert denied.status_code==403
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        response=public_declarations(request_for(RequestFactory(),payload(owner.email),ip="192.0.2.30"))
    body=json.loads(response.content)
    confirm_mail=next(m for m in mail.outbox if "confirm" in m.subject.lower())
    token=re.search(r"token=([^\s]+)",confirm_mail.body).group(1)
    calls=[]
    monkeypatch.setattr("django_core_micha.billing.stripe_service._stripe",lambda:__import__("types").SimpleNamespace(
        Subscription=__import__("types").SimpleNamespace(modify=lambda *a,**kw:calls.append((a,kw)))))
    declaration=Declaration.objects.get(reference=body["reference"]); received=declaration.received_at
    applied=confirm_public_declaration(token)
    declaration.refresh_from_db(); contract.refresh_from_db()
    assert applied is not None and declaration.status=="scheduled" and declaration.received_at==received
    assert declaration.confirmed_at and not declaration.confirm_token_hash
    assert calls and contract.cancel_effective_at==end
    assert confirm_public_declaration(token) is None
    invalid=public_declarations_confirm(request_for(RequestFactory(),{"token":"forged"},path="/public/declarations/confirm/",ip="192.0.2.31"))
    assert invalid.status_code==400 and json.loads(invalid.content)=={"code":"invalid_token"}


def test_contract_references_are_scoped_to_the_purchaser_and_app(declaration_config):
    owner=make_user(3); stranger=make_user(4)
    contract=make_contract(owner)
    factory=RequestFactory()
    owner_request=factory.get("/contracts/"); owner_request.user=owner
    listing=contracts_list(owner_request)
    assert contract.reference in listing.content.decode()
    other_request=factory.get("/contracts/"); other_request.user=stranger
    assert json.loads(contracts_list(other_request).content)=={"contracts":[]}
    # A request by another account cannot resolve the owner's contract reference.
    req=factory.post("/",data="{}",content_type="application/json"); req.user=stranger
    with pytest.raises(Http404): contract_cancel(req,contract.reference)
    foreign=make_contract(owner,app="other")
    req.user=owner
    with pytest.raises(Http404): contract_cancel(req,foreign.reference)


def test_new_contract_endpoints_are_hidden_when_policy_block_is_absent(request,monkeypatch):
    path=Path.cwd()/f".billing-test-{uuid4().hex}.yaml"
    path.write_text(YAML.replace("contract:\n  terms_version: '2026-11-04'\n  seller: 'Example Seller Ltd'\n  withdrawal_refund: full\n  public_declarations: true\n",""),encoding="utf-8")
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_FILE",str(path),raising=False)
    monkeypatch.setattr("django.conf.settings.DCM_BILLING_APP","app",raising=False)
    response=contracts_list(RequestFactory().get("/contracts/"))
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    assert response.status_code==404
    public=public_declarations(request_for(RequestFactory(),payload("person@example.com")))
    assert public.status_code==404
    owner=make_user(5); purchase=Purchase.objects.create(user=owner,kind="token_package",plan_key="tokens",stripe_object_id="pi-legacy",
        amount_paid_cents=1000,currency="usd",billing_country="US",purchased_at=timezone.now(),grant_references=[],tokens_granted=0)
    list_request=RequestFactory().get("/withdrawal/"); list_request.user=owner
    listed=json.loads(withdrawal_list(list_request).content)["purchases"][0]
    assert set(listed)=={"id","kind","plan_key","purchased_at","amount_paid_cents","currency","eligible","reason","withdrawn_at"}
    preview_request=RequestFactory().get("/withdrawal/"); preview_request.user=owner
    preview=json.loads(withdrawal_preview(preview_request,purchase.pk).content)
    assert set(preview)=={"eligible","reason","amount_paid_cents","currency","time_share_cents","token_share_cents","used_cents","refund_cents","calculation"}


def test_acknowledgement_failure_retries_without_changing_receipt(monkeypatch,declaration_config):
    declaration=Declaration.objects.create(app="app",kind="withdrawal",channel="public",name="Person",email="person@example.com")
    received=declaration.received_at
    monkeypatch.setattr("django_core_micha.billing.declarations.send_mail",lambda *a,**kw:0)
    from django_core_micha.billing.declarations import send_acknowledgement
    assert not send_acknowledgement(declaration.pk)
    declaration.refresh_from_db(); assert declaration.ack_email_status=="failed" and declaration.received_at==received
    monkeypatch.setattr("django_core_micha.billing.declarations.send_mail",lambda *a,**kw:1)
    retry_declaration_emails()
    declaration.refresh_from_db(); assert declaration.ack_email_status=="sent" and declaration.ack_sent_at and declaration.received_at==received


def test_expired_public_confirmation_is_rejected(monkeypatch,declaration_config):
    owner=make_user(6); contract=make_contract(owner)
    declaration=Declaration.objects.create(app="app",kind="cancellation",channel="public",user=owner,contract=contract,status="awaiting_confirmation")
    from django_core_micha.billing.declarations import _confirmation_token
    token=_confirmation_token(declaration)
    declaration.confirm_expires_at=timezone.now()-timedelta(seconds=1); declaration.save(update_fields=("confirm_token_hash","confirm_expires_at"))
    assert confirm_public_declaration(token) is None



def test_public_endpoint_echoes_validated_input_rejects_bad_input_and_throttles(declaration_config,django_capture_on_commit_callbacks):
    cache.clear(); factory=RequestFactory()
    body=payload("echo@example.com"); body["requested_end_date"]="2027-01-31"
    with django_capture_on_commit_callbacks(execute=True):
        response=public_declarations(request_for(factory,body,ip="198.51.100.1"))
    submitted=json.loads(response.content)["submitted"]
    assert submitted["email"]=="echo@example.com" and submitted["name"]=="Test Declarant" and submitted["requested_end_date"]=="2027-01-31"
    assert submitted["reason"]=="" and submitted["message"]=="Please cancel."
    long_name=payload("long@example.com"); long_name["name"]="n"*201
    assert public_declarations(request_for(factory,long_name,ip="198.51.100.2")).status_code==400
    no_reason=payload("reason@example.com"); no_reason["cancellation_type"]="extraordinary"; no_reason["reason"]=""
    assert public_declarations(request_for(factory,no_reason,ip="198.51.100.3")).status_code==400
    assert Declaration.objects.filter(email__in=("long@example.com","reason@example.com")).count()==0
    cache.clear()
    with django_capture_on_commit_callbacks(execute=True):
        codes=[public_declarations(request_for(factory,payload(f"flood{i}@example.com"),ip="198.51.100.9")).status_code for i in range(11)]
    assert codes[:10]==[201]*10 and codes[10]==429


def test_public_intake_mail_leaves_the_request_thread(monkeypatch):
    from django_core_micha.billing import declarations
    monkeypatch.undo()  # the autouse fixture makes mail inline; check the real helper here
    started=[]
    class FakeThread:
        def __init__(self,target,daemon): self.target=target; started.append(daemon)
        def start(self): pass
    monkeypatch.setattr(declarations.threading,"Thread",FakeThread)
    calls=[]
    declarations._in_background(lambda *args: calls.append(args),1)
    assert started==[True] and calls==[]


def test_batch_processing_touches_only_this_apps_records(declaration_config,monkeypatch):
    from django_core_micha.billing.contracts import apply_due_cancellations,resume_withdrawals
    owner=make_user(40); foreign=make_contract(owner,app="other")
    Declaration.objects.create(app="other",kind="cancellation",channel="account",name="x",email="x@example.com",contract=foreign,status="scheduled",
        cancellation_type="ordinary",effective_at=timezone.now()-timedelta(days=1))
    Declaration.objects.create(app="other",kind="withdrawal",channel="account",name="x",email="x@example.com",contract=foreign,status="accepted")
    Contract.objects.filter(pk=foreign.pk).update(status="withdrawn")
    Declaration.objects.create(app="other",kind="cancellation",channel="public",name="x",email="x@example.com",status="needs_review",ack_email_status="failed")
    mail.outbox.clear()
    performed=[]
    monkeypatch.setattr("django_core_micha.billing.contracts._perform_withdrawal",lambda pk: performed.append(pk))
    assert apply_due_cancellations()==0 and resume_withdrawals()==0 and performed==[]
    retry_declaration_emails()
    assert mail.outbox==[]
