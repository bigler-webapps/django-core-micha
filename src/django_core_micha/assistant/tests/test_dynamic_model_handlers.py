import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers.base import DriverError, _same_handler, invoke_tool
from django_core_micha.assistant.group_grants import catalogue, save_selection
from django_core_micha.assistant.hooks import ToolSpec, tools_for_chat
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db(transaction=True)


def test_fresh_model_handler_is_invoked_and_stale_fields_are_rejected(monkeypatch):
    model = get_user_model()
    owner = model.objects.create_user(username="dynamic-model-owner")
    target = model.objects.create_user(username="dynamic-model-target")
    chat = AssistantChat.objects.create(owner=owner)
    calls = []

    def read(self, user, chat, arguments, trust):
        calls.append(self)
        return self.username, []

    monkeypatch.setattr(model, "dynamic_read", read, raising=False)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"]}]}]

    def registry(_chat):
        instance = model.objects.get(pk=target.pk)
        return [ToolSpec("read", "Read", "Read", {}, instance.dynamic_read)]

    activate_catalogue(groups, deployment="dynamic-model", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="dynamic-model",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
                           DCM_ASSISTANT_TOOLS_FOR_CHAT=registry):
        save_selection(chat, catalogue(chat, owner), ["g"])
        original = tools_for_chat(chat)[0]
        assert async_to_sync(invoke_tool)(original, owner, chat, {}, "cloud") == (target.username, [])
        assert len(calls) == 1 and calls[0] is not original.handler.__self__

        model.objects.filter(pk=target.pk).update(username="changed-target")
        with pytest.raises(DriverError) as error:
            async_to_sync(invoke_tool)(original, owner, chat, {}, "cloud")
        assert error.value.code == "access_lost"
        assert len(calls) == 1


def test_model_equivalence_rejects_extra_state_deferred_fields_and_changed_method(monkeypatch):
    model = get_user_model()
    original = model.objects.create_user(username="model-bindings")

    def read(self, *args):
        return "ok", []

    def replacement(self, *args):
        return "changed", []

    monkeypatch.setattr(model, "dynamic_read", read, raising=False)
    left = model.objects.get(pk=original.pk)
    right = model.objects.get(pk=original.pk)
    assert _same_handler(left.dynamic_read, right.dynamic_read)
    right.captured_state = "different"
    assert not _same_handler(left.dynamic_read, right.dynamic_read)
    deferred = model.objects.only("pk").get(pk=original.pk)
    assert not _same_handler(left.dynamic_read, deferred.dynamic_read)
    assert not _same_handler(left.dynamic_read, replacement.__get__(right, model))
    right = model.objects.get(pk=original.pk)
    right._state.db = "other"
    assert not _same_handler(left.dynamic_read, right.dynamic_read)
