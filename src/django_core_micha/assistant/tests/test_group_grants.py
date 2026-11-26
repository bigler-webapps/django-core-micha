import copy

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grants import catalogue, catalogue_revision, effective_ids
from django_core_micha.assistant.group_grant_views import ChatGroupGrantsView
from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.views import ChatDetailView

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    user = get_user_model().objects.create_user(username="grant-owner")
    chat = AssistantChat.objects.create(owner=user)
    groups = [{"id": "planning", "label": "Planning", "kind": "read", "members": [
        {"id": "plans", "label": "Plans", "available": True, "tools": ["read_plans"]},
        {"id": "reviews", "label": "Reviews", "available": False, "unavailable_reason": "No access"},
    ]}]
    return user, chat, groups


def activate(groups, deployment="test", revision=0):
    activate_catalogue(copy.deepcopy(groups), deployment=deployment, expected_revision=revision)
    return {"DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE": lambda: copy.deepcopy(groups),
            "DCM_ASSISTANT_GROUP_DEPLOYMENT": deployment}


def call(user, chat, method="get", data=None, view=ChatGroupGrantsView):
    factory = APIRequestFactory()
    request = getattr(factory, method)("/", data, format="json")
    force_authenticate(request, user=user)
    return view.as_view()(request, chat_id=chat.pk)


def test_save_conflicts_and_member_snapshots(setup):
    user, chat, groups = setup
    identity = activate(groups)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups, **identity):
        first = call(user, chat).data
        data = {"group_ids": ["planning"], "grant_revision": first["grant_revision"],
                "catalogue_revision": first["catalogue_revision"]}
        saved = call(user, chat, "put", data)
        assert saved.status_code == 200
        assert saved.data["groups"][0]["members"][0]["effective"]
        assert "tools" not in saved.data["groups"][0]["members"][0]
        assert call(user, chat, "put", data).status_code == 409
        groups[0]["members"].append({"id": "new", "label": "New", "available": True})
        activate_catalogue(copy.deepcopy(groups), deployment="updated", expected_revision=1)
        from django.conf import settings
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "updated"
        fresh = call(user, chat).data
        assert not fresh["groups"][0]["members"][-1]["effective"]
        updated = {"group_ids": ["planning"], "grant_revision": fresh["grant_revision"],
                   "catalogue_revision": fresh["catalogue_revision"]}
        assert not call(user, chat, "put", updated).data["groups"][0]["members"][-1]["effective"]
        chat.refresh_from_db()
        groups[0]["members"][0]["tools"] = ["different"]
        activate_catalogue(copy.deepcopy(groups), deployment="changed", expected_revision=2)
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "changed"
        assert "plans" not in effective_ids(chat, catalogue(chat, user))


def test_private_archived_and_forged_requests(setup):
    user, chat, groups = setup
    other = get_user_model().objects.create_user(username="grant-other")
    identity = activate(groups)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups, **identity):
        assert call(other, chat).status_code == 404
        state = call(user, chat).data
        data = {"group_ids": ["forged"], "grant_revision": state["grant_revision"],
                "catalogue_revision": state["catalogue_revision"]}
        assert call(user, chat, "put", data).status_code == 400
        data["action_grants"] = ["plans"]
        assert call(user, chat, "put", data).status_code == 400
        assert call(user, chat, "patch", {"archived": True}, ChatDetailView).status_code == 200
        assert call(user, chat).status_code == 409
        assert call(user, chat, "put", data).status_code == 409


def test_validation_translation_and_opt_in(setup):
    user, chat, groups = setup
    assert call(user, chat).data == {"supported": False}
    identity = activate(groups)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups, **identity):
        original = catalogue_revision(catalogue(chat, user))
        groups[0]["label"] = "Planung"
        assert catalogue_revision(catalogue(chat, user)) == original
        from django.conf import settings
        authoritative = copy.deepcopy(groups)
        settings.DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE = lambda: authoritative
        groups[0]["members"].append(copy.copy(groups[0]["members"][0]))
        with pytest.raises(ValueError, match="invalid_group_catalogue"):
            catalogue(chat, user)
