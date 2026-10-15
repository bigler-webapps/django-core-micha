import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.views import ChatListCreateView

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def catalogue_settings():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver": "openai_api", "model": "gpt-4o-mini", "name": "Mini",
        "efforts": ["low", "medium", "high", "max"], "billing": "subscription", "default": True}]):
        yield


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="chat-list-user", password="secret")


def _chat(owner, title, archived=False):
    return AssistantChat.objects.create(
        owner=owner,
        title=title,
        selected_driver="openai_api",
        selected_model="gpt-4o-mini",
        selected_effort="medium",
        archived_at=timezone.now() if archived else None,
    )


def _list(user, path="/"):
    request = APIRequestFactory().get(path)
    force_authenticate(request, user=user)
    return ChatListCreateView.as_view()(request).data


def test_chat_lists_separate_active_archived_and_other_owners(user):
    active = _chat(user, "Active")
    archived = _chat(user, "Archived", archived=True)
    other = get_user_model().objects.create_user(username="chat-list-other", password="secret")
    _chat(other, "Other active")
    _chat(other, "Other archived", archived=True)

    active_data = _list(user)
    archived_data = _list(user, "/?archived=true")

    assert [row["id"] for row in active_data["results"]] == [str(active.pk)]
    assert [row["id"] for row in archived_data["results"]] == [str(archived.pk)]
    assert active_data.keys() == archived_data.keys() == {"results", "next", "has_more"}


def test_active_chat_cursor_paginates_only_active_chats(user):
    active = [_chat(user, f"Active {index}") for index in range(5)]
    archived = _chat(user, "Archived", archived=True)
    base_time = timezone.now()
    for index, chat in enumerate(active):
        AssistantChat.objects.filter(pk=chat.pk).update(updated_at=base_time - timezone.timedelta(days=index))

    first_page = _list(user, "/?limit=2")
    results = list(first_page["results"])
    assert first_page["has_more"] is True
    assert first_page["next"]

    cursor = first_page["next"]
    while cursor:
        page = _list(user, f"/?limit=2&cursor={cursor}")
        results.extend(page["results"])
        cursor = page["next"]

    assert [row["id"] for row in results] == [str(chat.pk) for chat in active]
    assert str(archived.pk) not in [row["id"] for row in results]
