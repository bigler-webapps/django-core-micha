import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.utils import timezone

from django_core_micha.billing import operator
from django_core_micha.billing.models import (AICostRecord, FreeAccess, Purchase, Subscription,
    TeamPackage, TeamSeat, TokenAccount, TokenEntry)
from django_core_micha.billing.services import access_state, token_balance

pytestmark=pytest.mark.django_db

@pytest.fixture(autouse=True)
def security_settings():
    with override_settings(SECURITY_DEFAULT_LEVEL="basic",SECURITY_LEVELS=("basic","strong"),INVITE_MIN_ROLE_LEVEL=2,AUTH_POLICY_WRITE_MIN_ROLE_LEVEL=3,INVITE_ADMIN_ROLES=(),ROLE_DEFINITIONS={"user":{"level":1}},DEFAULT_ROLE_CODE="user"):
        yield

def user(email, **kwargs): return get_user_model().objects.create_user(username=email,email=email,**kwargs)
def request(method,path,u,data=None,query=None):
    method=method.upper()
    rf=RequestFactory(); fn=getattr(rf,method.lower())
    if query and query.get("QUERY_STRING"): path += "?"+query["QUERY_STRING"]
    req=fn(path,data=json.dumps(data or {}) if method in ("POST","DELETE") else None,content_type="application/json"); req.user=u; req.session={"auth_level":"strong"}; return req
def body(response): return json.loads(response.content)
def sub(u,key="basic",end=None):
    return Subscription.objects.create(user=u,plan_key=key,stripe_subscription_id=f"sub-{u.pk}-{key}",status="active",current_period_end=end or timezone.now()+timedelta(days=10))

def test_all_operator_routes_guard_anonymous_and_regular_users():
    anon=type("Anonymous",(),{"is_authenticated":False})()
    normal=user("normal@example.test"); admin=user("operator@example.test",is_staff=True,is_superuser=True)
    for view,method,args in ((operator.overview,"get",()),(operator.accounts,"get",()),(operator.free_access,"post",(normal.pk,)),(operator.free_access,"delete",(normal.pk,))):
        assert view(request(method,"/operator/",anon),*args).status_code==401
        response=view(request(method,"/operator/",normal, data={"reason":"test"}),*args)
        assert response.status_code==403 and response["Cache-Control"]=="no-store"
    assert operator.overview(request("get","/",admin)).status_code==200

def test_overview_month_aggregates_scoped_rows_and_excludes_withdrawal_expiry():
    now=timezone.now(); start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    app="billing-test"
    with override_settings(DCM_BILLING_APP=app):
        a=user("sub@example.test"); b=user("team@example.test"); c=user("free@example.test")
        sub(a,"basic"); FreeAccess.objects.create(user=c,granted_by_label="op",reason="x")
        pkg=TeamPackage.objects.create(owner=user("owner@example.test"),plan_key="team",seats=3,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=3),stripe_ref="secret-team")
        TeamSeat.objects.create(package=pkg,user=b)
        for kind,key in (("subscription","sub"),("team_package","team"),("token_package","tokens")):
            Purchase.objects.create(user=a,kind=kind,plan_key=key,stripe_object_id=f"purchase-{key}",amount_paid_cents=10,currency="usd",purchased_at=now,tokens_granted=17 if kind=="token_package" else 0)
        account=TokenAccount.objects.create(user=a)
        for kind,amount,ref in (("grant",20,"g"),("purchase",5,"p"),("debit",-5,"d"),("expiry",-3,"e"),("expiry",-4,"withdrawal:x")):
            TokenEntry.objects.create(account=account,kind=kind,amount=amount,remaining=amount if kind in ("grant","purchase") else None,reference=ref,app=("" if kind in ("grant","purchase","expiry") else app),created_at=now)  # as the real code writes them: only debits carry the app
        for model,cost in (("m1",100),("m2",50)):
            AICostRecord.objects.create(provider="p",model=model,cost_usd_micros=cost,purpose="x",app=app,created_at=now)
        AICostRecord.objects.create(provider="foreign",model="other",cost_usd_micros=900,purpose="x",app="other-app",created_at=now)
        response=operator.overview(request("get","/",user("operator2@example.test",is_staff=True,is_superuser=True),query={"QUERY_STRING":f"month={start:%Y-%m}"}))
    assert response.status_code==200
    data=body(response)
    assert data["accounts"]=={"active":3,"by_source":{"subscription":{"basic":1},"team_seat":1,"free_access":1}}
    assert data["subscriptions"]=={"new":1,"ended":0}
    assert data["team"]=={"packages_active":1,"packages_sold":1,"seats_total":3,"seats_used":1}
    assert data["token_packages"]=={"sold":1,"tokens":17}
    assert data["tokens"]=={"granted":25,"used":5,"expired":3}
    assert data["ai_cost"]["usd_micros"]==150 and data["ai_cost"]["usd_micros_per_used_token"]==30
    assert [x["model"] for x in data["ai_cost"]["by_model"]]==["m1","m2"]
    assert "secret-team" not in response.content.decode().lower()

@pytest.mark.parametrize("query",[{"month":"2025-13"},{"month":"2025-1"},{"month":"9999-12"}])
def test_overview_rejects_invalid_month(query):
    admin=user("op-month@example.test",is_staff=True,is_superuser=True)
    assert operator.overview(request("get","/",admin,query={"QUERY_STRING":f"month={query['month']}"})).status_code==400

def test_account_list_search_pagination_parity_and_fixed_query_count(django_assert_num_queries):
    admin=user("op-list@example.test",is_staff=True,is_superuser=True)
    people=[user(f"person{i:02}@example.test",first_name=f"First{i}") for i in range(25)]
    sub(people[0]); FreeAccess.objects.create(user=people[1],granted_by_label="x",reason="r")
    responses=[]
    for size in (3,25):
        with django_assert_num_queries(6):
            responses.append(operator.accounts(request("get","/",admin,query={"QUERY_STRING":f"page_size={size}"})))
    first=body(responses[0]); full=body(responses[1])
    assert first["count"]==26 and first["page_size"]==3 and full["count"]==26
    for row in full["results"]:
        person=next(x for x in people+[admin] if x.pk==row["user_id"]); state=access_state(person)
        assert row["access"]=={"status":state.status,"source":state.source,"ends_at":state.ends_at.isoformat() if state.ends_at else None}
        assert row["token_balance"]==token_balance(person)
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"q=FIRST24"})))['count']==1
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"q=person01%40EXAMPLE.TEST"})))['count']==1
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"page=bad"})))['detail']=="invalid_page"
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"page_size=101"})))['detail']=="invalid_page"
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"q="+"x"*101})))['detail']=="invalid_query"

def test_free_access_grant_idempotence_revoke_retains_billing_rows_and_audit():
    from django_core_micha.auditlog.models import AuditEvent
    admin=user("auditor@example.test",is_staff=True,is_superuser=True); target=user("target@example.test"); sub(target,end=timezone.now()-timedelta(days=1))
    account=TokenAccount.objects.create(user=target); TokenEntry.objects.create(account=account,kind="grant",amount=4,remaining=4)
    for data in ({},{"reason":" "},{"reason":"x"*1001}): assert operator.free_access(request("post","/",admin,data),target.pk).status_code==400
    first=operator.free_access(request("post","/",admin,{"reason":"courtesy"}),target.pk)
    assert first.status_code==201 and body(first)["free_access"]["granted_by"]==admin.email
    assert operator.free_access(request("post","/",admin,{"reason":"other"}),target.pk).status_code==200
    assert FreeAccess.objects.filter(user=target,revoked_at__isnull=True).count()==1
    assert AuditEvent.objects.filter(actor=admin,event_type__icontains="freeaccess").exists()
    row=FreeAccess.objects.get(user=target); FreeAccess.objects.create(user=target,granted_by_label="legacy",reason="old")
    revoked=operator.free_access(request("delete","/",admin),target.pk)
    assert body(revoked)["revoked"] is True and FreeAccess.objects.filter(user=target,revoked_at__isnull=True).count()==0
    assert Subscription.objects.filter(user=target).exists() and TokenEntry.objects.filter(account=account).exists()
    assert access_state(target).status=="read_only"
    assert operator.free_access(request("delete","/",admin),target.pk).status_code==200
    assert operator.free_access(request("delete","/",admin),999999).status_code==404



def test_guard_runs_before_the_method_check_and_page_is_bounded():
    anon=type("Anonymous",(),{"is_authenticated":False})(); normal=user("m405@example.test"); admin=user("op405@example.test",is_staff=True,is_superuser=True)
    for method in ("put","patch"):
        assert operator.accounts(_with_method(method,anon)).status_code==401
        assert operator.accounts(_with_method(method,normal)).status_code==403
    resp=operator.accounts(_with_method("put",admin)); assert resp.status_code==405 and resp["Cache-Control"]=="no-store"
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"page=10001"})))["detail"]=="invalid_page"


def _with_method(method,u):
    req=getattr(RequestFactory(),method)("/operator/accounts/"); req.user=u; req.session={"auth_level":"strong"}; return req


def test_free_access_mutations_enforce_csrf():
    admin=user("csrf-op@example.test",is_staff=True,is_superuser=True); target=user("csrf-target@example.test")
    for method,data in (("post",{"reason":"x"}),("delete",None)):
        req=request(method,"/",admin,data); req._dont_enforce_csrf_checks=False
        assert operator.free_access(req,target.pk).status_code==403
    assert not FreeAccess.objects.filter(user=target).exists()


def test_revoke_is_audited_with_the_operator_and_revoke_without_grant_says_so():
    from django_core_micha.auditlog.models import AuditEvent
    admin=user("rev-op@example.test",is_staff=True,is_superuser=True); target=user("rev-target@example.test")
    assert body(operator.free_access(request("delete","/",admin),target.pk))["revoked"] is False
    operator.free_access(request("post","/",admin,{"reason":"courtesy"}),target.pk)
    before=AuditEvent.objects.filter(actor=admin).count()
    assert body(operator.free_access(request("delete","/",admin),target.pk))["revoked"] is True
    assert AuditEvent.objects.filter(actor=admin).count()>before


def test_overview_counts_an_ended_subscription_and_ignores_other_app_ledger_rows():
    now=timezone.now(); start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    admin=user("ov-op@example.test",is_staff=True,is_superuser=True); person=user("ov-person@example.test")
    row=sub(person,end=now-timedelta(days=1)); Subscription.objects.filter(pk=row.pk).update(status="canceled",ended_at=now)
    account=TokenAccount.objects.create(user=person)
    with override_settings(DCM_BILLING_APP="mine"):
        TokenEntry.objects.create(account=account,kind="debit",amount=-5,app="mine",reference="d1",created_at=now)
        TokenEntry.objects.create(account=account,kind="debit",amount=-99,app="other-app",reference="d2",created_at=now)
        TokenEntry.objects.create(account=account,kind="grant",amount=7,remaining=7,reference="g1",created_at=now)
        data=body(operator.overview(request("get","/",admin,query={"QUERY_STRING":f"month={start:%Y-%m}"})))
    assert data["subscriptions"]["ended"]==1 and data["tokens"]["used"]==5 and data["tokens"]["granted"]==7


def test_account_list_rows_match_access_state_for_every_kind_of_person():
    now=timezone.now(); admin=user("kinds-op@example.test",is_staff=True,is_superuser=True)
    expired=user("kinds-expired@example.test",first_name="Ex",last_name="Pired"); sub(expired,end=now-timedelta(days=2))
    seat_holder=user("kinds-seat@example.test"); pkg=TeamPackage.objects.create(owner=admin,plan_key="team",seats=2,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=5),stripe_ref="kinds-team"); TeamSeat.objects.create(package=pkg,user=seat_holder)
    revoked=user("kinds-revoked@example.test"); FreeAccess.objects.create(user=revoked,granted_by_label="op",reason="r",revoked_at=now-timedelta(hours=1))
    free=user("kinds-free@example.test"); FreeAccess.objects.create(user=free,granted_by_label="op@example.test",reason="because")
    paying=user("kinds-paying@example.test"); get_user_model().objects.filter(pk=paying.pk).update(username="uniqueuser77"); sub(paying,"pro")
    rows={r["user_id"]:r for r in body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"page_size=100"})))["results"]}
    for person in (expired,seat_holder,revoked,free,paying):
        state=access_state(person); row=rows[person.pk]
        assert row["access"]=={"status":state.status,"source":state.source,"ends_at":state.ends_at.isoformat() if state.ends_at else None}
    assert rows[paying.pk]["plan_key"]=="pro" and rows[paying.pk]["term_ends_at"]
    assert rows[seat_holder.pk]["plan_key"]=="team" and rows[free.pk]["free_access"]=={"reason":"because","granted_by":"op@example.test","granted_at":rows[free.pk]["free_access"]["granted_at"]}
    assert rows[expired.pk]["plan_key"] is None and rows[revoked.pk]["free_access"] is None
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"q=pired"})))["count"]==1
    assert body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":"q=UNIQUEUSER77"})))["count"]==1


def test_anonymous_mutation_is_401_before_csrf_is_considered():
    anon=type("Anonymous",(),{"is_authenticated":False})(); target=user("anon-target@example.test")
    for method,data in (("post",{"reason":"x"}),("delete",None)):
        req=request(method,"/",anon,data); req._dont_enforce_csrf_checks=False
        assert operator.free_access(req,target.pk).status_code==401


def test_routes_are_wired_to_the_operator_views():
    from django_core_micha.billing import urls
    routes={p.name:(str(p.pattern),p.callback) for p in urls.urlpatterns}
    assert routes["billing-operator-overview"]==("operator/overview/",operator.overview)
    assert routes["billing-operator-accounts"]==("operator/accounts/",operator.accounts)
    assert routes["billing-operator-free-access"][0]=="operator/accounts/<int:user_id>/free-access/" and routes["billing-operator-free-access"][1].__wrapped__ is not None


def test_pagination_returns_the_requested_page_and_an_empty_one_beyond_the_end():
    admin=user("page-op@example.test",is_staff=True,is_superuser=True)
    for i in range(5): user(f"page{i}@example.test")
    q=lambda s: body(operator.accounts(request("get","/",admin,query={"QUERY_STRING":s})))
    one,two,three=q("page_size=2&page=1"),q("page_size=2&page=2"),q("page_size=2&page=3")
    ids=lambda d:[r["user_id"] for r in d["results"]]
    assert len(ids(one))==2 and len(ids(two))==2 and len(ids(three))==2 and not (set(ids(one))&set(ids(two))) and not (set(ids(two))&set(ids(three)))
    assert one["count"]==6 and q("page_size=2&page=4")["results"]==[] and q("page_size=2&page=4")["count"]==6
    assert [r["email"] for r in one["results"]+two["results"]+three["results"]]==sorted(r["email"] for r in one["results"]+two["results"]+three["results"])


def test_no_stripe_id_reference_or_payload_in_any_operator_response():
    now=timezone.now(); admin=user("priv-op@example.test",is_staff=True,is_superuser=True); person=user("priv-person@example.test")
    Subscription.objects.create(user=person,plan_key="basic",stripe_subscription_id="sub_SECRETSTRIPEID",status="active",current_period_end=now+timedelta(days=5))
    account=TokenAccount.objects.create(user=person); TokenEntry.objects.create(account=account,kind="grant",amount=3,remaining=3,reference="stripe:LEDGER-REF-9",created_at=now)
    pkg=TeamPackage.objects.create(owner=admin,plan_key="team",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=5),stripe_ref="pi_SECRETREF"); TeamSeat.objects.create(package=pkg,user=person)
    Purchase.objects.create(user=person,kind="subscription",plan_key="basic",stripe_object_id="sub_SECRETSTRIPEID",stripe_payment_intent_id="pi_SECRETPI",amount_paid_cents=1,currency="usd",purchased_at=now)
    responses=[operator.overview(request("get","/",admin)),operator.accounts(request("get","/",admin)),
        operator.free_access(request("post","/",admin,{"reason":"r"}),person.pk),operator.free_access(request("delete","/",admin),person.pk)]
    text=" ".join(r.content.decode() for r in responses).lower()
    for word in ("secretstripeid","ledger-ref","secretref","secretpi","stripe","sk_","whsec","payload"):
        assert word not in text


def test_overview_has_constant_queries_model_fields_and_null_cost_without_usage(django_assert_max_num_queries):
    now=timezone.now(); admin=user("ovq-op@example.test",is_staff=True,is_superuser=True)
    for i in range(8):
        person=user(f"ovq{i}@example.test"); sub(person,"plan%d"%(i%3))
        AICostRecord.objects.create(provider="p",model=f"m{i%2}",cost_usd_micros=10,input_tokens=3,output_tokens=2,cache_tokens=1,purpose="x",app="",created_at=now)
    with override_settings(DCM_BILLING_APP=""):
        with django_assert_max_num_queries(20):
            data=body(operator.overview(request("get","/",admin)))
    assert data["ai_cost"]["usd_micros_per_used_token"] is None and data["accounts"]["active"]==8
    first=data["ai_cost"]["by_model"][0]
    assert set(first)=={"provider","model","calls","usd_micros","input_tokens","output_tokens","cache_tokens"} and first["calls"]==4 and first["usd_micros"]==40 and first["input_tokens"]==12
    assert data["accounts"]["by_source"]["subscription"]=={"plan0":3,"plan1":3,"plan2":2}
