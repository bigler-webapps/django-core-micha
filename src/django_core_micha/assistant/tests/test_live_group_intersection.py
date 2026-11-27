import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.group_grants import allowed_selectors, allowed_tools
from django_core_micha.assistant.group_grant_views import ChatGroupGrantsView
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db


def test_live_selection_and_wire_contract():
    user = get_user_model().objects.create_user(username="live-groups")
    other = get_user_model().objects.create_user(username="other-groups")
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "read", "label": "Read", "kind": "read", "members": [
        {"id": "one", "label": "One", "available": True, "tools": ["one"], "selectors": ["one"]},
        {"id": "two", "label": "Two", "available": False, "unavailable_reason": "Denied", "tools": ["two"]},
    ]}, {"id": "write", "label": "Write", "kind": "write", "members": [
        {"id": "three", "label": "Three", "available": True, "confirmation": "direct", "tools": ["three"]}]}]
    factory = APIRequestFactory()
    def call(actor, method, data=None):
        request = getattr(factory, method)("/", data, format="json")
        force_authenticate(request, actor)
        return ChatGroupGrantsView.as_view()(request, chat_id=chat.pk)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups):
        assert allowed_tools(chat, user) == {"one"}
        assert allowed_selectors(chat, user) == {"one"}
        first = call(user, "get").data
        payload = {"group_ids": ["read", "unknown"], "grant_revision": first["grant_revision"],
                   "catalogue_revision": "ignored", "reauthorize_group_ids": ["write"]}
        assert call(user, "put", payload).status_code == 200
        chat.refresh_from_db()
        assert chat.group_grants == ["read"]  # the unknown ID is dropped, never stored
        assert call(user, "put", payload).data["code"] == "stale_grants"
        assert call(other, "get").status_code == 404
        groups[0]["members"][1]["available"] = True
        assert allowed_tools(chat, user) == {"one", "two"}
        groups[0]["members"][0]["available"] = False
        groups[0]["members"][0]["unavailable_reason"] = "Denied"
        assert allowed_tools(chat, user) == {"two"}
        groups[0]["members"][0]["available"] = True
        assert allowed_tools(chat, user) == {"one", "two"}
        groups[0]["members"].append({"id": "new", "label": "New", "available": True, "tools": ["new"]})
        assert allowed_tools(chat, user) == {"one", "two", "new"}
        chat.archived_at = __import__("django.utils.timezone", fromlist=["now"]).now()
        chat.save(update_fields=["archived_at"])
        assert call(user, "get").status_code == 409
    assert allowed_tools(chat, user) is None
