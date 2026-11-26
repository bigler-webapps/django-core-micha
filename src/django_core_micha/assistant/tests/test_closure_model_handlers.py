import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers.base import DriverError, _same_handler, invoke_tool
from django_core_micha.assistant.group_grants import authorized_chat, catalogue, save_selection
from django_core_micha.assistant.hooks import ToolSpec, tools_for_chat
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db(transaction=True)


def make_handler(captured, calls, *, default=None):
    def handler(user, chat, arguments, trust, item=default):
        calls.append(captured)
        return captured.title, []
    return handler


def test_fresh_captured_chat_handler_is_invoked_and_changed_chat_denied():
    owner = get_user_model().objects.create_user(username="closure-owner")
    chat = AssistantChat.objects.create(owner=owner)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"]}]}]
    calls = []
    def registry(value):
        captured = AssistantChat.objects.get(pk=value.pk)
        return [ToolSpec("read", "Read", "Read", {}, make_handler(captured, calls))]
    activate_catalogue(groups, deployment="closure", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="closure",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=registry):
        save_selection(chat, catalogue(chat, owner), ["g"])
        original = tools_for_chat(chat)[0]
        assert async_to_sync(invoke_tool)(original, owner, chat, {}, "cloud") == (chat.title, [])
        assert calls[0] is not original.handler.__closure__[1].cell_contents
        AssistantChat.objects.filter(pk=chat.pk).update(title="changed")
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(original, owner, chat, {}, "cloud")
        assert len(calls) == 1


def test_closure_model_bindings_fail_closed_for_unsafe_state():
    owner = get_user_model().objects.create_user(username="closure-safety")
    chat = AssistantChat.objects.create(owner=owner, group_grants={"g": {"members": ["a"]}})
    a = AssistantChat.objects.get(pk=chat.pk)
    b = AssistantChat.objects.get(pk=chat.pk)
    calls = []
    assert _same_handler(make_handler(a, calls, default=a), make_handler(b, calls, default=b))
    b.group_grants["g"]["members"].append("b")
    assert not _same_handler(make_handler(a, calls), make_handler(b, calls))
    b = AssistantChat.objects.only("pk").get(pk=chat.pk)
    assert not _same_handler(make_handler(a, calls), make_handler(b, calls))
    b = AssistantChat.objects.get(pk=chat.pk)
    b.extra = object()
    assert not _same_handler(make_handler(a, calls), make_handler(b, calls))
    assert _same_handler(make_handler(a, []), make_handler(a, []))
    def replacement(*args):
        return "different", []
    assert not _same_handler(make_handler(a, calls), replacement)


def test_authorized_chat_closure_uses_fresh_actor_and_rejects_stale_or_changed_actor():
    owner = get_user_model().objects.create_user(username="decorated-owner")
    other = get_user_model().objects.create_user(username="decorated-other")
    chat = AssistantChat.objects.create(owner=owner)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"]}]}]
    calls = []

    def registry(value):
        return [ToolSpec("read", "Read", "Read", {}, make_handler(value, calls))]

    activate_catalogue(groups, deployment="decorated-closure", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="decorated-closure",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=registry):
        save_selection(chat, catalogue(chat, owner), ["g"])
        decorated = authorized_chat(chat.pk, owner)
        original = registry(decorated)[0]
        assert async_to_sync(invoke_tool)(original, owner, decorated, {}, "cloud") == (chat.title, [])
        assert calls[0] is not decorated
        assert calls[0]._authorized_actor.pk == owner.pk

        get_user_model().objects.filter(pk=owner.pk).update(username="changed-owner")
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(original, owner, decorated, {}, "cloud")

        changed = authorized_chat(chat.pk, owner)
        changed._authorized_actor = other
        changed_tool = registry(changed)[0]
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(changed_tool, owner, changed, {}, "cloud")
        assert len(calls) == 1
