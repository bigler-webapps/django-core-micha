import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant import group_grants, hooks
from django_core_micha.assistant.group_grant_views import ChatGroupGrantsView
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db


def test_live_intersection_offer_invocation_and_selectors():
    user = get_user_model().objects.create_user(username="live-coverage")
    chat = AssistantChat.objects.create(owner=user)
    groups = [
        {"id": "a", "label": "A", "kind": "read", "members": [
            {"id": "one", "label": "One", "available": True, "tools": ["one"], "selectors": ["one"]},
            {"id": "denied", "label": "Denied", "available": False, "unavailable_reason": "No access", "tools": ["denied"], "selectors": ["denied"]}]},
        {"id": "b", "label": "B", "kind": "read", "members": [
            {"id": "two", "label": "Two", "available": True, "tools": ["two"], "selectors": ["two"]}]},
        {"id": "w", "label": "W", "kind": "write", "members": [
            {"id": "write", "label": "Write", "available": True, "confirmation": "direct", "tools": ["write"]}]},
    ]
    registry = [hooks.ToolSpec(name, name, name, {}, lambda *_: ("ok", [])) for name in ("one", "two", "denied", "write", "new")]
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups, DCM_ASSISTANT_TOOL_REGISTRY=registry):
        assert {t.name for t in hooks.tools_for_chat(chat)} == {"one", "two"}
        assert group_grants.allowed_selectors(chat, user) == {"one", "two"}
        assert group_grants.tool_authorization(chat.pk, user, "write")[0] is False
        chat.group_grants = ["a", "unknown"]
        chat.save(update_fields=["group_grants"])
        assert {t.name for t in hooks.tools_for_chat(chat)} == {"one"}
        assert group_grants.allowed_selectors(chat, user) == {"one"}
        assert group_grants.tool_authorization(chat.pk, user, "one")[0] is True
        assert group_grants.tool_authorization(chat.pk, user, "two")[0] is False
        assert group_grants.tool_authorization(chat.pk, user, "denied")[0] is False
        groups[0]["members"].append({"id": "new", "label": "New", "available": True, "tools": ["new"], "selectors": ["new"]})
        assert group_grants.tool_authorization(chat.pk, user, "new")[0] is True
        assert group_grants.allowed_selectors(chat, user) == {"one", "new"}
        groups[0]["members"][0].update(available=False, unavailable_reason="Revoked")
        assert group_grants.tool_authorization(chat.pk, user, "one")[0] is False
        groups[0]["members"][0]["available"] = True
        assert group_grants.tool_authorization(chat.pk, user, "one")[0] is True


def test_owner_archive_shared_and_unregistered_contract():
    owner = get_user_model().objects.create_user(username="live-owner")
    other = get_user_model().objects.create_user(username="live-other")
    chat = AssistantChat.objects.create(owner=owner)
    groups = [{"id": "a", "label": "A", "kind": "read", "members": []}]
    factory = APIRequestFactory()
    def call(actor, method, data=None):
        request = getattr(factory, method)("/", data, format="json")
        force_authenticate(request, actor)
        return ChatGroupGrantsView.as_view()(request, chat_id=chat.pk)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups, DCM_ASSISTANT_SHARED_SCOPE=lambda scope: scope == "shared", DCM_ASSISTANT_SCOPE_PERMISSION=lambda user, scope: True):
        payload = {"group_ids": ["a", "unknown"], "grant_revision": "0", "catalogue_revision": "ignored", "reauthorize_group_ids": ["unknown"]}
        assert call(other, "get").status_code == 404
        assert call(other, "put", payload).status_code == 404
        response = call(owner, "put", payload)
        assert response.status_code == 200
        assert response.data["groups"][0]["enabled"] is True
        stale = call(owner, "put", payload)
        assert stale.status_code == 409 and stale.data["code"] == "stale_grants"
        chat.refresh_from_db()
        assert chat.group_grants == ["a"]
        chat.scope = "shared"
        chat.save(update_fields=["scope"])
        assert call(owner, "get").status_code == 404
        assert call(owner, "put", payload).status_code == 404
        chat.refresh_from_db()
        assert chat.group_grants == ["a"]
        assert group_grants.allowed_tools(chat, owner) is None
        assert group_grants.allowed_selectors(chat, owner) is None
        chat.scope = ""
        chat.archived_at = timezone.now()
        chat.save(update_fields=["scope", "archived_at"])
        assert call(owner, "get").status_code == 409
        assert call(owner, "put", payload).status_code == 409
    chat.archived_at = None
    chat.save(update_fields=["archived_at"])
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=None):
        assert call(owner, "get").data == {"supported": False}
        assert group_grants.allowed_tools(chat, owner) is None
        assert group_grants.allowed_selectors(chat, owner) is None
