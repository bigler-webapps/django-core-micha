import asyncio
import copy
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from django_core_micha.assistant import group_grants, hooks
from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers.base import DriverError, invoke_tool
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def grant_case():
    user = get_user_model().objects.create_user(username="round-one-owner")
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "old", "label": "Old", "available": True, "tools": ["read"]},
    ]}]
    return user, chat, groups


def test_new_member_requires_disable_and_enable(grant_case):
    user, chat, groups = grant_case
    activate_catalogue(groups, deployment="initial", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="initial",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ["g"])
        groups[0]["members"].append({"id": "new", "label": "New", "available": True})
        activate_catalogue(groups, deployment="updated", expected_revision=1)
        from django.conf import settings
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "updated"
        current = group_grants.catalogue(chat, user)
        assert group_grants.effective_ids(chat, current) == {"old"}
        group_grants.save_selection(chat, current, [])
        group_grants.save_selection(chat, current, ["g"])
        assert group_grants.effective_ids(chat, current) == {"old", "new"}


def test_baseline_does_not_expand_when_catalogue_changes(grant_case):
    user, chat, groups = grant_case
    initial = group_grants.validate_catalogue(groups)
    baseline = {"g": {"old": group_grants.member_revision(initial[0], initial[0]["members"][0])}}
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE=baseline):
        activate_catalogue(groups, deployment="initial", expected_revision=0)
    groups[0]["members"].append({"id": "new", "label": "New", "available": True})
    activate_catalogue(groups, deployment="updated", expected_revision=1)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="updated"):
        assert group_grants.effective_ids(chat, group_grants.catalogue(chat, user)) == {"old"}


def test_observed_removal_does_not_restore_saved_grant(grant_case):
    user, chat, groups = grant_case
    activate_catalogue(groups, deployment="initial", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="initial",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ["g"])
        removed = copy.deepcopy(groups[0]["members"].pop())
        activate_catalogue(groups, deployment="removed", expected_revision=1)
        from django.conf import settings
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "removed"
        current = group_grants.catalogue(chat, user)
        group_grants.save_selection(chat, current, ["g"])
        groups[0]["members"].append(removed)
        activate_catalogue(groups, deployment="returned", expected_revision=2)
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "returned"
        assert group_grants.effective_ids(chat, group_grants.catalogue(chat, user)) == set()


def test_inactive_actor_is_denied_even_when_scope_allows(grant_case):
    user, chat, _ = grant_case
    user.is_active = False
    user.save(update_fields=["is_active"])
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda *_: True):
        with pytest.raises(PermissionDenied):
            group_grants.authorized_chat(chat.pk, user)


def test_replaced_tool_never_invokes_stale_handler(grant_case):
    user, chat, groups = grant_case
    calls = []
    old = hooks.ToolSpec("read", "Read", "Read", {}, lambda *_: (calls.append("old") or "old", []))
    current = hooks.ToolSpec("read", "Read", "Read", {}, lambda *_: (calls.append("current") or "current", []))
    activate_catalogue(groups, deployment="initial", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="initial",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_TOOL_REGISTRY=[current]):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ["g"])
        with pytest.raises(DriverError) as error:
            async_to_sync(invoke_tool)(old, user, chat, {}, "cloud")
        assert error.value.code == "access_lost"
    assert calls == []


def test_grant_revocation_cancels_live_stream(grant_case, monkeypatch):
    from django_core_micha.assistant import turns
    from django_core_micha.assistant.models import AssistantTurn
    user, chat, groups = grant_case
    chat.selected_driver, chat.selected_model, chat.selected_effort = "openai_api", "test", "medium"
    chat.save(update_fields=["selected_driver", "selected_model", "selected_effort"])
    entered = asyncio.Event()
    class Driver:
        async def stream(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()
            yield {"type": "delta", "text": "unreachable"}
    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "_make_driver", lambda *_: Driver())
    async def run():
        task = asyncio.create_task(turns.run_turn(chat, user, "hello", AsyncMock()))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await sync_to_async(AssistantChat.objects.filter(pk=chat.pk).update)(group_grants={}, grant_revision=2)
        with pytest.raises(turns.AccessLost):
            await asyncio.wait_for(task, timeout=5)
    models = [{"driver": "openai_api", "model": "test", "name": "test",
               "efforts": ["medium"], "billing": "pay_per_use", "default": True}]
    activate_catalogue(groups, deployment="initial", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="initial",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_CATALOGUE=models,
                           DCM_ASSISTANT_LIMITS={"access_recheck_seconds": 1}):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ["g"])
        async_to_sync(run)()
    assert AssistantTurn.objects.get(chat=chat).state == "cancelled"
