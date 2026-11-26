import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant import group_grants, hooks
from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.models import AssistantChat


pytestmark = pytest.mark.django_db


@pytest.fixture
def chat():
    owner = get_user_model().objects.create_user(username="context-owner")
    return AssistantChat.objects.create(owner=owner, group_grants={})


def test_context_receives_only_effective_selectors(chat):
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "kept", "label": "Kept", "available": True, "selectors": ["kept"]},
        {"id": "removed", "label": "Removed", "available": False,
         "unavailable_reason": "No access", "selectors": ["withdrawn"]},
    ]}]
    activate_catalogue(groups, deployment="context", expected_revision=0)
    def context(chat, user, message, turn, *, allowed_selectors):
        return " ".join(sorted(allowed_selectors))
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="context", DCM_ASSISTANT_TURN_CONTEXT=context):
        group_grants.save_selection(chat, group_grants.catalogue(chat, chat.owner), ["g"])
        assert hooks.build_turn_context(chat, chat.owner, "", None) == "kept"


def test_legacy_context_is_suppressed_only_for_opted_in_private_chat(chat):
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=None,
                           DCM_ASSISTANT_TURN_CONTEXT=lambda *_: "legacy"):
        assert hooks.build_turn_context(chat, chat.owner, "", None) == "legacy"
    activate_catalogue([], deployment="context", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: [],
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: [],
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="context",
                           DCM_ASSISTANT_TURN_CONTEXT=lambda *_: "withdrawn"):
        assert hooks.build_turn_context(chat, chat.owner, "", None) is None


def test_opted_in_none_tool_hook_does_not_restore_global_registry(chat):
    activate_catalogue([], deployment="context", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: [],
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="context",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: [],
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=lambda _: None,
                           DCM_ASSISTANT_TOOL_REGISTRY=[hooks.ToolSpec("x", "x", "x", {}, lambda *_: None)]):
        assert hooks.tools_for_chat(chat) == []


def test_variadic_context_hook_does_not_bypass_explicit_selector_check(chat):
    calls = []
    def context(*args, **kwargs):
        calls.append((args, kwargs))
        return "withdrawn"
    activate_catalogue([], deployment="context", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: [],
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="context",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: [],
                           DCM_ASSISTANT_TURN_CONTEXT=context):
        assert hooks.build_turn_context(chat, chat.owner, "", None) is None
    assert calls == []
