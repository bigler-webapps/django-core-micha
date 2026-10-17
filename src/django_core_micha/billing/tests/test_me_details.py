import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from django_core_micha.billing.models import Subscription, TeamPackage, TeamSeat, TokenAccount, TokenEntry
from django_core_micha.billing.views import me

pytestmark=pytest.mark.django_db

def user(email,**kwargs): return get_user_model().objects.create_user(username=email,email=email,**kwargs)
def get(u,monkeypatch):
    from django_core_micha.billing import views
    monkeypatch.setattr(views,"_plans",lambda:[{"key":"known","name":"Known Plan"},{"key":"team","name":"Team Plan"}])
    req=RequestFactory().get("/me/"); req.user=u; req.session={"auth_level":"strong"}
    return json.loads(me(req).content)

def test_subscription_details_and_token_grants_are_private_and_sorted(monkeypatch):
    u=user("me@example.test"); now=timezone.now()
    Subscription.objects.create(user=u,plan_key="unknown-key",stripe_subscription_id="secret-sub",status="active",current_period_start=now-timedelta(days=1),current_period_end=now+timedelta(days=3))
    account=TokenAccount.objects.create(user=u)
    TokenEntry.objects.create(account=account,kind="grant",amount=10,remaining=4,expires_at=now+timedelta(days=1),reference="private-ledger-reference",purpose="plan:basic")
    TokenEntry.objects.create(account=account,kind="purchase",amount=8,remaining=8,expires_at=now+timedelta(days=2),reference="private-package-reference",purpose="purchase purpose")
    TokenEntry.objects.create(account=account,kind="grant",amount=9,remaining=0)
    TokenEntry.objects.create(account=account,kind="grant",amount=7,remaining=7,expires_at=now-timedelta(seconds=1))
    data=get(u,monkeypatch); serialized=json.dumps(data).lower()
    assert data["subscription"]["plan_name"]=="" and data["subscription"]["renews"] is True
    assert [x["source"] for x in data["tokens"]["grants"]]==["plan","package"]
    grants=data["tokens"]["grants"]
    assert [x["origin"] for x in grants]==["plan","package"]
    assert (grants[0]["amount"],grants[0]["remaining"])==(10,4) and (grants[1]["amount"],grants[1]["remaining"])==(8,8)
    assert grants[0]["expires_at"].startswith((now+timedelta(days=1)).strftime("%Y-%m-%d")) and grants[1]["expires_at"].startswith((now+timedelta(days=2)).strftime("%Y-%m-%d"))
    assert all("id" not in x and "reference" not in x for x in data["tokens"]["grants"])
    assert all("purpose" not in x for x in grants)
    assert data["tokens"]["pool"] is None
    assert not any(word in serialized for word in ("stripe","payload","private-ledger-reference","private-package-reference","purchase purpose","plan:basic","sk_","whsec"))

def test_team_payer_sees_holders_holder_sees_only_seat_and_nonmember_sees_none(monkeypatch):
    now=timezone.now(); owner=user("owner-me@example.test",first_name="Team",last_name="Owner"); holder=user("holder-me@example.test",first_name="Seat",last_name="Holder"); other=user("other-me@example.test")
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=3,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=3),stripe_ref="private-stripe-ref")
    TeamSeat.objects.create(package=pkg,user=holder)
    payer_data=get(owner,monkeypatch); holder_data=get(holder,monkeypatch); other_data=get(other,monkeypatch)
    assert payer_data["team"]["seat_holders"]==[{"user_id":holder.pk,"name":"Seat Holder","email":holder.email}]
    assert holder_data["team"] is None and holder_data["seat"]["plan_key"]=="team"
    assert "seat_holders" not in holder_data["seat"] and "email" not in holder_data["seat"]
    assert holder_data["seat"]["payer_name"]=="Team Owner"
    assert other_data["team"] is None and "seat" not in other_data
    assert "seat" not in payer_data
    assert "private-stripe-ref" not in json.dumps(payer_data)


def test_origin_mapping_covers_plan_trial_credit_and_package(monkeypatch):
    from django_core_micha.billing.views import _grant_origin
    assert _grant_origin(TokenEntry(kind="grant",purpose="plan:basic"))=="plan"
    assert _grant_origin(TokenEntry(kind="grant",purpose="plan:team"))=="plan"
    assert _grant_origin(TokenEntry(kind="purchase",purpose="anything"))=="package"
    assert _grant_origin(TokenEntry(kind="grant",purpose="trial"))=="trial"
    assert _grant_origin(TokenEntry(kind="grant",purpose="operator credit"))=="credit"
    assert _grant_origin(TokenEntry(kind="grant",purpose=""))=="credit"


def test_grant_origins_and_existing_response_keys(monkeypatch):
    u=user("origins@example.test"); now=timezone.now()
    Subscription.objects.create(user=u,plan_key="known",stripe_subscription_id="origin-sub",status="active",current_period_start=now,current_period_end=now+timedelta(days=4))
    account=TokenAccount.objects.create(user=u)
    rows=[("grant","plan:basic","plan"),("grant","trial","trial"),("grant","operator credit","credit"),("grant","","credit"),("purchase","","package")]
    for i,(kind,purpose,_) in enumerate(rows):
        TokenEntry.objects.create(account=account,kind=kind,amount=5,remaining=3,purpose=purpose,reference=f"secret-entry-ref-{i}")
    data=get(u,monkeypatch)
    assert set(data)=={"access","tokens","plans","subscription","team"}
    assert set(data["tokens"])=={"balance","grants","pool"}
    assert set(data["access"])=={"status","source","ends_at"}
    assert data["subscription"]["plan_key"]=="known" and data["subscription"]["plan_name"]=="Known Plan"
    assert data["team"] is None and data["tokens"]["balance"]==15
    assert [g["source"] for g in data["tokens"]["grants"]]==["plan"]*4+["package"]
    assert [g["origin"] for g in data["tokens"]["grants"]]==["plan","trial","credit","credit","package"]
    assert all(set(g)=={"source","origin","amount","remaining","expires_at"} for g in data["tokens"]["grants"])
    encoded=json.dumps(data)
    assert "secret-entry-ref-" not in encoded and "purpose" not in encoded and "plan:basic" not in encoded


def _pool_entry(pkg,kind,amount,remaining,expires_at=None,reference="private-pool-reference",purpose="private-pool-purpose"):
    account,_=TokenAccount.objects.get_or_create(team_package=pkg)
    return TokenEntry.objects.create(account=account,kind=kind,amount=amount,remaining=remaining,expires_at=expires_at,reference=reference,purpose=purpose)


def test_pool_for_payer_holder_used_up_and_personal_balance_stays_separate(monkeypatch):
    now=timezone.now(); owner=user("pool-owner@example.test"); holder=user("pool-holder@example.test")
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=2,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=5),stripe_ref="pool-team-ref")
    TeamSeat.objects.create(package=pkg,user=holder)
    _pool_entry(pkg,"grant",20,7,now+timedelta(days=2))
    _pool_entry(pkg,"purchase",10,0,now+timedelta(days=3),"private-used-ref")
    personal=TokenAccount.objects.create(user=owner)
    TokenEntry.objects.create(account=personal,kind="grant",amount=4,remaining=2,purpose="trial")
    expected={"plan_key":"team","plan_name":"Team Plan","amount":30,"remaining":7,"expires_at":pkg.ends_at.isoformat()}
    payer_data=get(owner,monkeypatch); holder_data=get(holder,monkeypatch)
    assert payer_data["tokens"]["pool"]==expected and holder_data["tokens"]["pool"]==expected
    assert payer_data["tokens"]["balance"]==2 and payer_data["tokens"]["grants"]==[{"source":"plan","origin":"trial","amount":4,"remaining":2,"expires_at":None}]
    assert holder_data["tokens"]["grants"]==[] and holder_data["tokens"]["balance"]==0
    assert "private-pool-reference" not in json.dumps(payer_data) and "private-pool-purpose" not in json.dumps(holder_data)
    TokenEntry.objects.filter(account__team_package=pkg,kind="grant").update(remaining=0)
    assert get(owner,monkeypatch)["tokens"]["pool"]["remaining"]==0


def test_pool_absence_expiry_ended_packages_and_owned_package_precedence(monkeypatch):
    now=timezone.now(); plain=user("plain-pool@example.test")
    assert get(plain,monkeypatch)["tokens"]["pool"] is None
    owner=user("expired-pool@example.test")
    expired=TeamPackage.objects.create(owner=owner,plan_key="team",seats=1,starts_at=now-timedelta(days=4),ends_at=now+timedelta(days=4),stripe_ref="expired-pool-package")
    _pool_entry(expired,"grant",3,3,now-timedelta(seconds=1))
    assert get(owner,monkeypatch)["tokens"]["pool"] is None
    no_account_owner=user("no-pool-account@example.test")
    TeamPackage.objects.create(owner=no_account_owner,plan_key="team",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=1),stripe_ref="no-pool-account-package")
    assert get(no_account_owner,monkeypatch)["tokens"]["pool"] is None
    ended_owner=user("ended-owner@example.test"); ended_holder=user("ended-holder@example.test")
    ended=TeamPackage.objects.create(owner=ended_owner,plan_key="team",seats=1,starts_at=now-timedelta(days=4),ends_at=now-timedelta(seconds=1),stripe_ref="ended-pool-package")
    TeamSeat.objects.create(package=ended,user=ended_holder)
    _pool_entry(ended,"grant",8,8)
    assert get(ended_owner,monkeypatch)["tokens"]["pool"] is None
    assert get(ended_holder,monkeypatch)["tokens"]["pool"] is None
    dual=user("dual-role@example.test"); holder_owner=user("holder-payer@example.test"); holder=user("dual-holder@example.test")
    owned=TeamPackage.objects.create(owner=dual,plan_key="known",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=3),stripe_ref="owned-pool")
    held_pkg=TeamPackage.objects.create(owner=holder_owner,plan_key="team",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=4),stripe_ref="held-pool")
    TeamSeat.objects.create(package=held_pkg,user=dual)
    _pool_entry(owned,"grant",6,6)
    _pool_entry(held_pkg,"grant",9,9)
    assert get(dual,monkeypatch)["tokens"]["pool"]["plan_key"]=="known"
    payer=user("full-name-absent@example.test",first_name="",last_name="")
    email_only=TeamPackage.objects.create(owner=payer,plan_key="team",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=1),stripe_ref="email-payer")
    TeamSeat.objects.create(package=email_only,user=holder)
    assert get(holder,monkeypatch)["seat"]["payer_name"]==payer.email
    payer.email=""; payer.save(update_fields=["email"])
    assert get(holder,monkeypatch)["seat"]["payer_name"]==payer.username


def test_team_response_regression_and_payer_name_fallbacks(monkeypatch):
    now=timezone.now(); owner=user("named-owner@example.test",first_name="  Alex ",last_name=" Member "); holder=user("named-holder@example.test")
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=2,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=2),stripe_ref="named-team")
    TeamSeat.objects.create(package=pkg,user=holder)
    payer_data=get(owner,monkeypatch); holder_data=get(holder,monkeypatch)
    assert set(payer_data)=={"access","tokens","plans","subscription","team"}
    assert set(payer_data["team"])=={"plan_key","plan_name","ends_at","seats_total","seats_used","seat_holders"}
    assert payer_data["team"]["plan_key"]=="team" and payer_data["team"]["plan_name"]=="Team Plan"
    assert payer_data["team"]["seats_total"]==2 and payer_data["team"]["seats_used"]==1
    assert payer_data["team"]["seat_holders"]==[{"user_id":holder.pk,"name":"","email":holder.email}]
    assert "seat" not in payer_data
    assert set(holder_data["seat"])=={"plan_key","plan_name","ends_at","payer_name"}
    assert holder_data["seat"]["payer_name"]=="Alex   Member"
    assert holder_data["team"] is None and "seat_holders" not in holder_data["seat"]
    assert owner.email not in json.dumps(holder_data)


def test_payer_query_count_stays_constant_with_more_holders_and_grants(monkeypatch,django_assert_max_num_queries):
    now=timezone.now(); owner=user("query-owner@example.test")
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=12,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=3),stripe_ref="query-team")
    _pool_entry(pkg,"grant",20,20)
    personal=TokenAccount.objects.create(user=owner)
    for i in range(4): TokenEntry.objects.create(account=personal,kind="grant",amount=2,remaining=2,purpose="plan:basic")
    for i in range(2): TeamSeat.objects.create(package=pkg,user=user(f"query-holder-{i}@example.test"))
    with django_assert_max_num_queries(13): get(owner,monkeypatch)
    for i in range(2,12): TeamSeat.objects.create(package=pkg,user=user(f"query-holder-{i}@example.test"))
    with django_assert_max_num_queries(13): get(owner,monkeypatch)



def test_subscription_block_known_plan_cancellation_and_selection(monkeypatch):
    u=user("sel@example.test"); now=timezone.now()
    Subscription.objects.create(user=u,plan_key="known",stripe_subscription_id="old-sub",status="canceled",ended_at=now-timedelta(days=30),current_period_end=now-timedelta(days=30))
    Subscription.objects.create(user=u,plan_key="known",stripe_subscription_id="cur-sub",status="active",cancel_at_period_end=True,current_period_start=now-timedelta(days=2),current_period_end=now+timedelta(days=5))
    block=get(u,monkeypatch)["subscription"]
    assert block["plan_name"]=="Known Plan" and block["status"]=="active" and block["cancel_at_period_end"] is True and block["renews"] is False
    assert block["current_period_end"].startswith((now+timedelta(days=5)).strftime("%Y-%m-%d"))
    assert get(user("none@example.test"),monkeypatch)["subscription"] is None


def test_pool_counts_team_allowance_and_perpetual_entries_but_not_other_ledger_kinds(monkeypatch):
    now = timezone.now(); owner = user("elig-owner@example.test")
    pkg = TeamPackage.objects.create(owner=owner, plan_key="team", seats=2, starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=5), stripe_ref="elig-team-ref")
    _pool_entry(pkg, "grant", 30, 12, now + timedelta(days=4), "stripe:team:elig-team-ref", "plan:team")  # the real team allowance
    _pool_entry(pkg, "grant", 5, 5, None, "elig-perpetual", "operator credit")  # no expiry still counts
    _pool_entry(pkg, "grant", 99, 99, now - timedelta(seconds=1), "elig-expired", "plan:old")  # expired does not
    _pool_entry(pkg, "debit", -7, None, None, "elig-debit", "spend")
    _pool_entry(pkg, "expiry", -3, None, None, "elig-expiry", "expiry")
    _pool_entry(pkg, "adjustment", 40, 40, None, "elig-adjustment", "adjustment")
    pool = get(owner, monkeypatch)["tokens"]["pool"]
    assert (pool["amount"], pool["remaining"]) == (35, 17)
    assert pool["plan_key"] == "team" and pool["plan_name"] == "Team Plan"
    assert "elig-" not in json.dumps(get(owner, monkeypatch)) and "plan:team" not in json.dumps(get(owner, monkeypatch))


def test_payer_name_never_falls_back_to_the_database_id(monkeypatch):
    now = timezone.now(); owner = user("noname-owner@example.test"); holder = user("noname-holder@example.test")
    get_user_model().objects.filter(pk=owner.pk).update(email="", first_name="", last_name="")
    pkg = TeamPackage.objects.create(owner=owner, plan_key="team", seats=1, starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=5), stripe_ref="noname-team-ref")
    TeamSeat.objects.create(package=pkg, user=holder)
    payer_name = get(holder, monkeypatch)["seat"]["payer_name"]
    assert payer_name == "noname-owner@example.test" and payer_name != str(owner.pk)  # the username is the last allowed fallback
