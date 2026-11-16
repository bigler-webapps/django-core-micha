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

def test_trial_first_debits_trial_before_expiring_code_grants_and_handles_partial_debits():
    u=user("trial-first@example.com"); now=timezone.now()
    code=grant_tokens(u,4,expires_at=now+timedelta(days=1),reference="code-grant",purpose="benefit_code")
    trial=grant_tokens(u,3,expires_at=None,reference="trial-grant",purpose="trial")

    debit_tokens(u,2,purpose="chat",reference="trial-first-1",precedence="trial_first")
    trial.refresh_from_db(); code.refresh_from_db()
    assert trial.remaining==1 and code.remaining==4

    debit_tokens(u,3,purpose="chat",reference="trial-first-2",precedence="trial_first")
    trial.refresh_from_db(); code.refresh_from_db()
    assert trial.remaining==0 and code.remaining==2

@pytest.mark.parametrize("kind", ["purchase", "adjustment"])
def test_trial_first_precedes_other_expiring_credit_kinds(kind):
    u=user(f"trial-before-{kind}@example.com")
    trial=grant_tokens(u,2,reference="trial",purpose="trial")
    other=TokenEntry.objects.create(account=trial.account,kind=kind,amount=2,remaining=2,expires_at=timezone.now()+timedelta(days=1),reference=f"other-{kind}")
    debit_tokens(u,1,purpose="chat",reference="trial-before-other",precedence="trial_first")
    trial.refresh_from_db(); other.refresh_from_db()
    assert (trial.remaining,other.remaining)==(1,2)

def test_default_debit_precedence_stays_expiration_first_for_all_credit_kinds():
    u=user("default-order@example.com"); now=timezone.now()
    code=grant_tokens(u,2,expires_at=now+timedelta(days=1),reference="default-code",purpose="benefit_code")
    trial=grant_tokens(u,2,reference="default-trial",purpose="trial")
    purchase=TokenEntry.objects.create(account=trial.account,kind="purchase",amount=2,remaining=2,expires_at=now+timedelta(days=2),reference="default-purchase")
    adjustment=TokenEntry.objects.create(account=trial.account,kind="adjustment",amount=2,remaining=2,expires_at=now+timedelta(days=3),reference="default-adjustment")

    debit_tokens(u,2,purpose="chat",reference="default-expiry-first")
    for entry in (code,trial,purchase,adjustment): entry.refresh_from_db()
    assert code.remaining==0
    assert (trial.remaining,purchase.remaining,adjustment.remaining)==(2,2,2)
    debit_tokens(u,3,purpose="chat",reference="default-next-credits")
    for entry in (trial,purchase,adjustment): entry.refresh_from_db()
    assert (trial.remaining,purchase.remaining,adjustment.remaining)==(2,0,1)

def test_trial_first_excludes_expired_credits_and_insufficient_debit_is_atomic():
    u=user("trial-expiry@example.com"); now=timezone.now()
    expired=grant_tokens(u,5,expires_at=now-timedelta(seconds=1),reference="expired-trial",purpose="trial")
    code=grant_tokens(u,3,expires_at=now+timedelta(days=1),reference="live-code",purpose="benefit_code")

    with pytest.raises(InsufficientTokens):
        debit_tokens(u,4,purpose="chat",reference="too-large",precedence="trial_first")
    expired.refresh_from_db(); code.refresh_from_db()
    assert expired.remaining==5 and code.remaining==3
    assert not TokenEntry.objects.filter(account=code.account,kind="debit",reference="too-large").exists()

    debit_tokens(u,3,purpose="chat",reference="live-only",precedence="trial_first")
    expired.refresh_from_db(); code.refresh_from_db()
    assert expired.remaining==5 and code.remaining==0

def test_debit_precedence_rejects_invalid_values_before_creating_an_account():
    from django_core_micha.billing.models import TokenAccount
    u=user("invalid-order@example.com")
    with pytest.raises(ValueError,match="unsupported_debit_precedence"):
        debit_tokens(u,1,purpose="chat",reference="invalid-order",precedence="trialfirst")
    assert not TokenAccount.objects.filter(user=u).exists()

def test_trial_first_preserves_debit_retry_and_reference_conflict_semantics():
    u=user("trial-retry@example.com")
    trial=grant_tokens(u,5,reference="retry-trial",purpose="trial")
    debit=debit_tokens(u,3,purpose="chat",reference="retry-debit",precedence="trial_first")
    assert debit_tokens(u,3,purpose="ignored-on-retry",reference="retry-debit",precedence="trial_first").pk==debit.pk
    trial.refresh_from_db(); assert trial.remaining==2
    with pytest.raises(ValueError,match="debit_reference_reused"):
        debit_tokens(u,2,purpose="chat",reference="retry-debit",precedence="trial_first")
    trial.refresh_from_db(); assert trial.remaining==2

def test_trial_first_keeps_personal_accounts_and_team_pools_isolated():
    owner=user("pool-owner@example.com"); other=user("pool-other@example.com"); now=timezone.now()
    package=TeamPackage.objects.create(owner=owner,plan_key="team",seats=1,starts_at=now,ends_at=now+timedelta(days=30),stripe_ref="trial-first-team")
    owner_trial=grant_tokens(owner,2,reference="owner-trial",purpose="trial")
    other_trial=grant_tokens(other,3,reference="other-trial",purpose="trial")
    pool_trial=grant_tokens(package,4,reference="pool-trial",purpose="trial")

    debit_tokens(owner,2,purpose="chat",reference="owner-only",precedence="trial_first")
    debit_tokens(package,3,purpose="team-chat",reference="pool-only",precedence="trial_first")
    owner_trial.refresh_from_db(); other_trial.refresh_from_db(); pool_trial.refresh_from_db()
    assert (owner_trial.remaining,other_trial.remaining,pool_trial.remaining)==(0,3,1)

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
