import hashlib
from datetime import timedelta
import pytest, yaml
from django.contrib.auth import get_user_model
from django.test import override_settings, Client
from django.utils import timezone
from django.urls import path
from django.conf import settings
from django.test.utils import override_settings as os
from django_core_micha.billing import views
from django_core_micha.billing.models import TeamPackage, TeamSeat, Subscription, FreeAccess, AICostRecord, TokenEntry
from django_core_micha.billing.services import (access_state, token_balance, grant_tokens, credit_purchase, debit_tokens, expire_tokens, InsufficientTokens, record_ai_call)

pytestmark=pytest.mark.django_db

def user(email): return get_user_model().objects.create_user(username=email,email=email,password="x")

def test_yaml_validated_and_rejects_duplicate(tmp_path):
    cfg={"app":"maana","currency":"usd","plans":[{"key":"p_month","kind":"subscription","name":"Personal","amount_cents":100,"interval":"month","tax_code":"txcd_1"}],"token_rates":{"chat":2}}
    file=tmp_path/"billing.yaml"; file.write_text(yaml.safe_dump(cfg),encoding="utf8")
    with override_settings(DCM_BILLING_APP="maana",DCM_BILLING_FILE=str(file)):
        from django_core_micha.billing.config import load_billing_config
        assert load_billing_config().plans[0].key=="p_month"
        cfg["plans"].append(dict(cfg["plans"][0])); file.write_text(yaml.safe_dump(cfg),encoding="utf8")
        with pytest.raises(ValueError): load_billing_config()

def test_access_subscription_team_free_and_seat_limit():
    owner=user("a@example.com"); member=user("b@example.com"); other=user("c@example.com"); now=timezone.now()
    sub=Subscription.objects.create(user=owner,plan_key="m",stripe_subscription_id="sub_1",status="active",current_period_end=now+timedelta(days=5))
    assert access_state(owner).status=="active" and access_state(owner).source=="subscription"
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=3),stripe_ref="team_1")
    response=views.team_seats
    from django.test import RequestFactory
    import json
    rf=RequestFactory(); req=rf.post("/team/seats/",data=json.dumps({"user_id":member.pk}),content_type="application/json"); req.user=owner
    assert response(req).status_code==201
    req=rf.post("/team/seats/",data=json.dumps({"user_id":other.pk}),content_type="application/json"); req.user=owner
    assert response(req).status_code==409
    assert access_state(member).source=="team"
    TeamSeat.objects.filter(package=pkg,user=member).update(active=False,removed_at=timezone.now())
    assert access_state(member).status=="read_only" and access_state(member).ends_at is not None
    FreeAccess.objects.create(user=member,granted_by_label="ops",reason="trial")
    assert access_state(member).source=="free"

def test_token_grant_expiry_purchase_and_idempotent_debits():
    u=user("tokens@example.com"); now=timezone.now()
    grant_tokens(u,10,expires_at=now+timedelta(seconds=1),reference="g1",purpose="term")
    credit_purchase(u,7,reference="buy1")
    assert token_balance(u)==17
    debit_tokens(u,5,purpose="chat",reference="d1")
    debit_tokens(u,5,purpose="chat",reference="d1")
    assert token_balance(u)==12
    with pytest.raises(InsufficientTokens): debit_tokens(u,13,purpose="chat",reference="d2")
    assert expire_tokens(now+timedelta(days=1))==1
    assert token_balance(u)==7
    assert TokenEntry.objects.filter(kind="expiry").count()==1

def test_cost_record_and_report_has_only_aggregates():
    from django.test import RequestFactory
    u=user("private@example.com")
    with override_settings(DCM_BILLING_APP="maana"):
        r=record_ai_call(user=u,provider="openai",model="gpt-4o-mini",input_tokens=1000,output_tokens=1000,purpose="chat",reference="t1")
    assert r.cost_usd_micros==750
    token="report-secret"; digest=hashlib.sha256(token.encode()).hexdigest()
    with override_settings(DCM_BILLING_REPORT_TOKEN_SHA256=[digest],DCM_BILLING_APP="maana"):
        rf=RequestFactory(); assert views.cost_report(rf.get("/cost-report/")).status_code==401
        req=rf.get("/cost-report/",HTTP_AUTHORIZATION="Bearer wrong"); assert views.cost_report(req).status_code==401
        req=rf.get("/cost-report/",HTTP_AUTHORIZATION=f"Bearer {token}"); response=views.cost_report(req)
    assert response.status_code==200
    import json
    data=json.loads(response.content); assert data["calls"]==1 and data["ai_cost_usd_micros"]==750
    assert "private@example.com" not in response.content.decode() and "user" not in data
