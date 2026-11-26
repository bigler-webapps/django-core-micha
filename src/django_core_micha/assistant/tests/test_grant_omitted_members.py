import copy

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grants import catalogue, effective_ids, save_selection
from django_core_micha.assistant.models import AssistantChat


@pytest.mark.django_db
def test_omission_does_not_lose_grant_or_approve_new_or_retired_member():
    groups = [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "a", "label": "A", "available": True},
        {"id": "b", "label": "B", "available": True},
        {"id": "c", "label": "C", "available": True},
    ]}]
    display = copy.deepcopy(groups)
    display[0]["members"][2].update(available=False, unavailable_reason="Unavailable")
    user = get_user_model().objects.create_user(username="omitted-member")
    chat = AssistantChat.objects.create(owner=user)
    activate_catalogue(groups, deployment="one", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: display,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="one"):
        save_selection(chat, catalogue(chat, user), ["g"])
        saved = copy.deepcopy(chat.group_grants)
        display[0]["members"] = copy.deepcopy(groups[0]["members"][1:])
        save_selection(chat, catalogue(chat, user), ["g"])
        assert chat.group_grants == saved
        display[0]["members"] = copy.deepcopy(groups[0]["members"])
        assert effective_ids(chat, catalogue(chat, user)) == {"a", "b"}

    retired = copy.deepcopy(groups)
    retired[0]["members"] = retired[0]["members"][1:]
    activate_catalogue(retired, deployment="two", expected_revision=1)
    activate_catalogue(groups, deployment="three", expected_revision=2)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: display,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="three"):
        chat.refresh_from_db()
        assert effective_ids(chat, catalogue(chat, user)) == {"b"}
