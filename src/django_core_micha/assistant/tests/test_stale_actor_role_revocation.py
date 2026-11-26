import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers.base import DriverError, invoke_tool
from django_core_micha.assistant.group_grants import allowed_selectors, catalogue, save_selection
from django_core_micha.assistant.hooks import ToolSpec, build_turn_context
from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.provider_context import context_token


@pytest.mark.django_db(transaction=True)
def test_revoked_role_is_not_retained_by_stale_caller():
    user = get_user_model().objects.create_user(username="stale-role", is_superuser=True)
    chat = AssistantChat.objects.create(owner=user)
    calls = []

    def groups(chat, actor):
        return [{"id": "data", "label": "Data", "kind": "read", "members": [
            {"id": "read", "label": "Read", "available": actor.is_superuser,
             "unavailable_reason": "Role revoked", "tools": ["read_data"],
             "selectors": ["data"]}]}]

    def handler(actor, chat, arguments, trust_class):
        calls.append(actor.is_superuser)
        return "secret", []

    def context(chat, actor, message, turn, allowed_selectors):
        return (actor.is_superuser, allowed_selectors)

    tool = ToolSpec("read_data", "Read", "Read data", {}, handler)
    authoritative = groups(chat, user)
    activate_catalogue(authoritative, deployment="role", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: authoritative,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="role",
                           DCM_ASSISTANT_GROUP_CATALOGUE=groups,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool],
                           DCM_ASSISTANT_TURN_CONTEXT=context):
        save_selection(chat, catalogue(chat, user), ["data"])
        before = context_token(chat.pk, user)
        assert allowed_selectors(chat, user) == {"data"}
        get_user_model().objects.filter(pk=user.pk).update(is_superuser=False)
        assert user.is_superuser  # The caller still holds the old object.
        assert context_token(chat.pk, user) != before
        assert allowed_selectors(chat, user) == set()
        assert build_turn_context(chat, user, "", None) == (False, frozenset())
        with pytest.raises(DriverError, match="access_lost"):
            async_to_sync(invoke_tool)(tool, user, chat, {}, "trusted")
        assert calls == []
