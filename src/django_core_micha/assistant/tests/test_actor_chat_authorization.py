from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone

from django_core_micha.assistant import group_grants, hooks, provider_context
from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers.base import DriverError, invoke_tool
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup_chat():
    users = get_user_model().objects
    owner = users.create_user(username="contract-owner")
    other = users.create_user(username="contract-other")
    chat = AssistantChat.objects.create(owner=owner)
    return owner, other, chat


def groups(chat, user):
    return [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True,
         "tools": ["read"], "selectors": ["safe"]}]}]


def test_missing_foreign_and_archived_inputs_are_denied(setup_chat):
    owner, other, chat = setup_chat
    calls = []
    tool = hooks.ToolSpec("read", "Read", "Read", {}, lambda *args: (calls.append(args) or "secret", []))
    context = lambda *args, allowed_selectors: calls.append(args) or "secret"
    activate_catalogue(groups(None, None), deployment="actor", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups(None, None),
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="actor",
                           DCM_ASSISTANT_GROUP_CATALOGUE=groups,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool], DCM_ASSISTANT_TURN_CONTEXT=context):
        for actor, candidate in [(None, chat), (owner, None), (other, chat)]:
            with pytest.raises(PermissionDenied):
                group_grants.allowed_selectors(candidate, actor)
            with pytest.raises(PermissionDenied):
                hooks.build_turn_context(candidate, actor, "", None)
            with pytest.raises(DriverError, match="access_lost"):
                async_to_sync(invoke_tool)(tool, actor, candidate, {}, "cloud")
        chat.archived_at = timezone.now()
        chat.save(update_fields=["archived_at"])
        with pytest.raises(PermissionDenied):
            provider_context.context_token(chat.pk, owner)
        with pytest.raises(PermissionDenied):
            group_grants.allowed_selectors(chat, owner)
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(tool, owner, chat, {}, "cloud")
    assert calls == []


def test_context_and_execution_use_live_chat_state(setup_chat):
    owner, _, chat = setup_chat
    seen = []
    def handler(user, current, arguments, trust):
        seen.append(current.scope)
        return "ok", []
    tool = hooks.ToolSpec("read", "Read", "Read", {}, handler)
    def context(current, user, message, turn, *, allowed_selectors):
        return current.scope, allowed_selectors
    activate_catalogue(groups(None, None), deployment="actor", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups(None, None),
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="actor",
                           DCM_ASSISTANT_GROUP_CATALOGUE=groups,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool], DCM_ASSISTANT_TURN_CONTEXT=context):
        group_grants.save_selection(chat, group_grants.catalogue(chat, owner), ["g"])
        forged = SimpleNamespace(pk=chat.pk, scope="shared", group_grants={})
        assert group_grants.allowed_selectors(forged, owner) == {"safe"}
        assert hooks.build_turn_context(forged, owner, "", None) == ("", frozenset({"safe"}))
        assert async_to_sync(invoke_tool)(tool, owner, forged, {}, "cloud") == ("ok", [])
        assert seen == [""]
        AssistantChat.objects.filter(pk=chat.pk).update(group_grants={})
        assert group_grants.allowed_selectors(forged, owner) == set()
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(tool, owner, forged, {}, "cloud")


def test_legacy_and_authorized_shared_chat_compatibility(setup_chat):
    owner, other, chat = setup_chat
    tool = hooks.ToolSpec("read", "Read", "Read", {}, lambda *args: ("legacy", []))
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=None,
                           DCM_ASSISTANT_TURN_CONTEXT=lambda *args: "legacy"):
        assert hooks.build_turn_context(None, None, "", None) == "legacy"
        assert async_to_sync(invoke_tool)(tool, None, None, {}, "cloud") == ("legacy", [])
        assert group_grants.allowed_selectors(None, None) is None
    chat.scope = "shared"
    chat.save(update_fields=["scope"])
    activate_catalogue(groups(None, None), deployment="actor", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups(None, None),
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="actor",
                           DCM_ASSISTANT_GROUP_CATALOGUE=groups,
                           DCM_ASSISTANT_SHARED_SCOPE=lambda scope: scope == "shared",
                           DCM_ASSISTANT_SCOPE_PERMISSION=lambda user, scope: True,
                           DCM_ASSISTANT_TURN_CONTEXT=lambda *args: "shared"):
        assert group_grants.allowed_selectors(chat, other) is None
        assert hooks.build_turn_context(chat, other, "", None) == "shared"
        assert async_to_sync(invoke_tool)(tool, other, chat, {}, "cloud") == ("legacy", [])
        assert provider_context.context_token(chat.pk, other) is None


def test_shared_discovery_does_not_reauthorize_owner(setup_chat):
    owner, other, chat = setup_chat
    chat.scope = "shared"
    chat.save(update_fields=["scope"])
    tool = hooks.ToolSpec("read", "Read", "Read", {}, lambda *args: ("ok", []))
    activate_catalogue(groups(None, None), deployment="actor", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups(None, None),
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="actor",
                           DCM_ASSISTANT_GROUP_CATALOGUE=groups,
                           DCM_ASSISTANT_SHARED_SCOPE=lambda scope: scope == "shared",
                           DCM_ASSISTANT_SCOPE_PERMISSION=lambda user, scope: user.pk == other.pk,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=lambda current: [tool]):
        assert hooks.tools_for_chat(chat) == [tool]
        assert group_grants.allowed_tools(chat, other) is None


def test_empty_scope_legacy_tool_fallback_still_obeys_grants(setup_chat):
    owner, _, chat = setup_chat
    tool = hooks.ToolSpec("read", "Read", "Read", {}, lambda *args: ("ok", []))
    activate_catalogue(groups(None, None), deployment="actor", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups(None, None),
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="actor",
                           DCM_ASSISTANT_GROUP_CATALOGUE=groups,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=lambda current: None,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        assert not hooks.is_shared_scope("")
        assert hooks.tools_for_chat(chat) == []
        group_grants.save_selection(chat, group_grants.catalogue(chat, owner), ["g"])
        assert hooks.tools_for_chat(chat) == [tool]
