import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers.base import DriverError, invoke_tool
from django_core_micha.assistant.group_grants import (
    allowed_selectors, catalogue, member_revision, save_selection, validate_catalogue,
)
from django_core_micha.assistant.hooks import ToolSpec, tools_for_chat
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db(transaction=True)


def test_discovery_and_revocation_during_handler():
    user = get_user_model().objects.create_user(username="runtime-grants")
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"]}]}]

    def handler(user, chat, arguments, trust):
        current = AssistantChat.objects.get(pk=chat.pk)
        save_selection(current, catalogue(current, user), [])
        return "withdrawn marker", []

    tool = ToolSpec("read", "Read", "Read", {}, handler)
    activate_catalogue(groups, deployment="runtime", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="runtime",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        assert tools_for_chat(chat) == []
        save_selection(chat, catalogue(chat, user), ["g"])
        assert tools_for_chat(chat) == [tool]
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(tool, user, chat, {}, "cloud")
        chat.refresh_from_db()
        assert tools_for_chat(chat) == []
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(tool, user, chat, {}, "cloud")


@pytest.mark.parametrize("kind", ["method", "closure"])
def test_dynamic_registry_accepts_equivalent_fresh_handlers(kind):
    user = get_user_model().objects.create_user(username="dynamic-" + kind)
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"]}]}]
    calls = []

    class Service:
        def read(self, user, chat, arguments, trust):
            calls.append("method")
            return "ok", []

    service = Service()

    def registry(_chat):
        def read(user, chat, arguments, trust):
            calls.append("closure")
            return "ok", []
        return [ToolSpec("read", "Read", "Read", {}, service.read if kind == "method" else read)]

    activate_catalogue(groups, deployment="runtime", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="runtime",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=registry):
        save_selection(chat, catalogue(chat, user), ["g"])
        tool = tools_for_chat(chat)[0]
        assert async_to_sync(invoke_tool)(tool, user, chat, {}, "cloud") == ("ok", [])
    assert calls == [kind]


def test_dynamic_registry_rejects_changed_closure_binding():
    user = get_user_model().objects.create_user(username="dynamic-replaced")
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"]}]}]
    calls = []
    version = ["old"]

    def registry(_chat):
        binding = version[0]
        def read(user, chat, arguments, trust):
            calls.append(binding)
            return binding, []
        return [ToolSpec("read", "Read", "Read", {}, read)]

    activate_catalogue(groups, deployment="runtime", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="runtime",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=registry):
        save_selection(chat, catalogue(chat, user), ["g"])
        old = tools_for_chat(chat)[0]
        version[0] = "new"
        with pytest.raises(DriverError) as error:
            async_to_sync(invoke_tool)(old, user, chat, {}, "cloud")
    assert error.value.code == "access_lost"
    assert calls == []


@pytest.mark.parametrize("baseline", [False, True])
def test_retirement_and_reintroduction_fence_real_capabilities(baseline):
    user = get_user_model().objects.create_user(username="lifecycle-" + str(baseline))
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True,
         "tools": ["read"], "selectors": ["data"]}]}]
    validated = validate_catalogue(groups)
    approved = {"g": {"m": member_revision(validated[0], validated[0]["members"][0])}}
    calls = []
    tool = ToolSpec("read", "Read", "Read", {}, lambda *_: (calls.append("read") or "ok", []))
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE=approved if baseline else {}):
        activate_catalogue(groups, deployment="initial", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="initial",
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        if not baseline:
            save_selection(chat, catalogue(chat, user), ["g"])
        assert tools_for_chat(chat) == [tool]
        assert allowed_selectors(chat, user) == {"data"}
        assert async_to_sync(invoke_tool)(tool, user, chat, {}, "cloud") == ("ok", [])
    # No chat reads or writes occur between the two activations.
    activate_catalogue([], deployment="removed", expected_revision=1)
    activate_catalogue(groups, deployment="returned", expected_revision=2)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="returned",
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        assert tools_for_chat(chat) == []
        assert allowed_selectors(chat, user) == set()
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(tool, user, chat, {}, "cloud")
        save_selection(chat, catalogue(chat, user), [])
        save_selection(chat, catalogue(chat, user), ["g"])
        assert tools_for_chat(chat) == [tool]
        assert allowed_selectors(chat, user) == {"data"}
        assert async_to_sync(invoke_tool)(tool, user, chat, {}, "cloud") == ("ok", [])
    assert calls == ["read", "read"]
