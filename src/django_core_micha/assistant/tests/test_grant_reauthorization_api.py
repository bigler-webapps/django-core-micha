import copy

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grant_views import ChatGroupGrantsView
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db


def test_api_explicit_reauthorization_and_omitted_field():
    user = get_user_model().objects.create_user(username="reauthorize-owner")
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "old", "label": "Old", "available": True}]}]
    activate_catalogue(copy.deepcopy(groups), deployment="before", expected_revision=0)
    factory = APIRequestFactory()
    def call(method, data=None):
        request = getattr(factory, method)("/", data, format="json")
        force_authenticate(request, user=user)
        return ChatGroupGrantsView.as_view()(request, chat_id=chat.pk)
    def save(ids, **extra):
        state = call("get").data
        response = call("put", {"group_ids": ids, "grant_revision": state["grant_revision"],
                                "catalogue_revision": state["catalogue_revision"], **extra})
        assert response.status_code == 200
        return response.data
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: copy.deepcopy(groups),
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="before",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: copy.deepcopy(groups)):
        assert save(["g"])["groups"][0]["members"][0]["effective"]
        groups[0]["members"].append({"id": "new", "label": "New", "available": True})
        activate_catalogue(copy.deepcopy(groups), deployment="after", expected_revision=1)
        from django.conf import settings
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "after"
        assert not save(["g"])["groups"][0]["members"][1]["effective"]
        assert save(["g"], reauthorize_group_ids=["g"])["groups"][0]["members"][1]["effective"]
        save([])
        assert save(["g"], reauthorize_group_ids=["g"])["groups"][0]["members"][1]["effective"]
