import pytest
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from django_core_micha.billing.models import Subscription, TeamPackage, TeamSeat
from django_core_micha.billing.services import access_state, debit_tokens, grant_tokens, token_balance
from django_core_micha.billing.services import InsufficientTokens

pytestmark=pytest.mark.django_db

def _user(email): return get_user_model().objects.create_user(username=email,email=email)

def test_subscription_status_mapping_ended_access_stays_read_only():
    now=timezone.now()
    for status in ("active","trialing","past_due"):
        u=_user(f"{status}@example.com")
        Subscription.objects.create(user=u,plan_key="p",stripe_subscription_id=f"sub-{status}",status=status,current_period_end=now+timedelta(days=1))
        assert access_state(u).status=="active"
    for status in ("canceled","unpaid","incomplete_expired"):
        u=_user(f"{status}@example.com")
        Subscription.objects.create(user=u,plan_key="p",stripe_subscription_id=f"sub-{status}",status=status,current_period_end=now-timedelta(days=100),ended_at=now-timedelta(days=100))
        assert access_state(u).status=="read_only" and access_state(u).ends_at is not None
    for status in ("incomplete","paused"):
        u=_user(f"{status}@example.com")
        Subscription.objects.create(user=u,plan_key="p",stripe_subscription_id=f"sub-{status}",status=status,current_period_end=now+timedelta(days=1))
        assert access_state(u).status=="read_only"

def test_team_removal_and_non_owner_cannot_manage():
    from django.test import RequestFactory
    from django_core_micha.billing.views import team_seats
    import json
    owner=_user("owner2@example.com"); member=_user("member2@example.com"); stranger=_user("stranger2@example.com"); now=timezone.now()
    pkg=TeamPackage.objects.create(owner=owner,plan_key="team",seats=1,starts_at=now-timedelta(days=1),ends_at=now+timedelta(days=2),stripe_ref="team-second")
    TeamSeat.objects.create(package=pkg,user=member)
    req=RequestFactory().delete("/team/seats/",data=json.dumps({"email":member.email}),content_type="application/json"); req.user=stranger
    assert team_seats(req).status_code==404
    req=RequestFactory().delete("/team/seats/",data=json.dumps({"email":member.email}),content_type="application/json"); req.user=owner
    import json as jsonlib
    assert jsonlib.loads(team_seats(req).content)["removed"] is True
    assert not TeamSeat.objects.filter(package=pkg,active=True).exists()

def test_debit_interleaving_never_spends_past_balance():
    u=_user("race@example.com"); grant_tokens(u,10,reference="interleave",purpose="test")
    debit_tokens(u,7,purpose="first",reference="first")
    with pytest.raises(InsufficientTokens): debit_tokens(u,7,purpose="second",reference="second")
    assert token_balance(u)==3

