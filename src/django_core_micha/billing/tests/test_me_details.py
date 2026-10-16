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
    TokenEntry.objects.create(account=account,kind="grant",amount=10,remaining=4,expires_at=now+timedelta(days=1),reference="private-ledger-reference")
    TokenEntry.objects.create(account=account,kind="purchase",amount=8,remaining=8,expires_at=now+timedelta(days=2))
    TokenEntry.objects.create(account=account,kind="grant",amount=9,remaining=0)
    TokenEntry.objects.create(account=account,kind="grant",amount=7,remaining=7,expires_at=now-timedelta(seconds=1))
    data=get(u,monkeypatch); serialized=json.dumps(data).lower()
    assert data["subscription"]["plan_name"]=="" and data["subscription"]["renews"] is True
    assert [x["source"] for x in data["tokens"]["grants"]]==["plan","package"]
    grants=data["tokens"]["grants"]
    assert (grants[0]["amount"],grants[0]["remaining"])==(10,4) and (grants[1]["amount"],grants[1]["remaining"])==(8,8)
    assert grants[0]["expires_at"].startswith((now+timedelta(days=1)).strftime("%Y-%m-%d")) and grants[1]["expires_at"].startswith((now+timedelta(days=2)).strftime("%Y-%m-%d"))
    assert all("id" not in x and "reference" not in x for x in data["tokens"]["grants"])
    assert not any(word in serialized for word in ("stripe","payload","private-ledger-reference","sk_","whsec"))

def test_team_payer_sees_holders_holder_sees_only_seat_and_nonmember_sees_none(monkeypatch):
    now=timezone.now(); owner=user("owner-me@example.test",first_name="Team",last_name="Owner"); holder=user("holder-me@example.test",first_name="Seat",last_name="Holder"); other=user("other-me@example.test")
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=3,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=3),stripe_ref="private-stripe-ref")
    TeamSeat.objects.create(package=pkg,user=holder)
    payer_data=get(owner,monkeypatch); holder_data=get(holder,monkeypatch); other_data=get(other,monkeypatch)
    assert payer_data["team"]["seat_holders"]==[{"user_id":holder.pk,"name":"Seat Holder","email":holder.email}]
    assert holder_data["team"] is None and holder_data["seat"]["plan_key"]=="team"
    assert "seat_holders" not in holder_data["seat"] and "email" not in holder_data["seat"]
    assert other_data["team"] is None and "seat" not in other_data
    assert "private-stripe-ref" not in json.dumps(payer_data)



def test_subscription_block_known_plan_cancellation_and_selection(monkeypatch):
    u=user("sel@example.test"); now=timezone.now()
    Subscription.objects.create(user=u,plan_key="known",stripe_subscription_id="old-sub",status="canceled",ended_at=now-timedelta(days=30),current_period_end=now-timedelta(days=30))
    Subscription.objects.create(user=u,plan_key="known",stripe_subscription_id="cur-sub",status="active",cancel_at_period_end=True,current_period_start=now-timedelta(days=2),current_period_end=now+timedelta(days=5))
    block=get(u,monkeypatch)["subscription"]
    assert block["plan_name"]=="Known Plan" and block["status"]=="active" and block["cancel_at_period_end"] is True and block["renews"] is False
    assert block["current_period_end"].startswith((now+timedelta(days=5)).strftime("%Y-%m-%d"))
    assert get(user("none@example.test"),monkeypatch)["subscription"] is None
