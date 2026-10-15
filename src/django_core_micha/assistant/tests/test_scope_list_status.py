import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.views import AvailabilityView, ChatListCreateView

pytestmark = pytest.mark.django_db

@pytest.fixture
def user(): return get_user_model().objects.create_user(username="scope-user", password="secret")

def test_scoped_list_filters_and_forbidden_scope_is_404(user):
    a=AssistantChat.objects.create(owner=user,scope="alpha")
    AssistantChat.objects.create(owner=user,scope="beta")
    factory=APIRequestFactory()
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s:s!="blocked"):
        request=factory.get("/assistant/chats/?scope=alpha"); force_authenticate(request,user=user)
        response=ChatListCreateView.as_view()(request)
    assert [item["id"] for item in response.data["results"]] == [str(a.pk)]
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s:s!="blocked"):
        request=factory.get("/assistant/chats/?scope=blocked"); force_authenticate(request,user=user)
        response=ChatListCreateView.as_view()(request)
    assert response.status_code == 404 and response.data == {"code":"not_found","error":"not_found"}

def test_status_reports_app_availability_reason(user, monkeypatch):
    from django_core_micha.assistant import views
    monkeypatch.setattr(views,"availability",lambda:{"status":"available","providers":{"openai_api":"available"}})
    with override_settings(DCM_ASSISTANT_AVAILABILITY=lambda u,s:"data_not_approved",DCM_ASSISTANT_SCOPE_PERMISSION=lambda u,s:True):
        request=APIRequestFactory().get("/assistant/status/?scope=alpha"); force_authenticate(request,user=user)
        response=AvailabilityView.as_view()(request)
    assert response.data == {"status":"unavailable","reason":"data_not_approved","providers":{"openai_api":"available"}}


def test_status_with_a_forbidden_scope_is_404_and_a_cursor_does_not_cross_scopes(user):
    from rest_framework.test import APIRequestFactory, force_authenticate
    from django_core_micha.assistant.models import AssistantChat
    from django_core_micha.assistant.views import AvailabilityView, ChatListCreateView

    for _ in range(3): AssistantChat.objects.create(owner=user, scope="a")
    AssistantChat.objects.create(owner=user, scope="b")
    factory = APIRequestFactory()
    with override_settings(DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: s in {"a", "b"}):
        request = factory.get("/", {"scope": "a", "limit": 1}); force_authenticate(request, user=user)
        cursor = ChatListCreateView.as_view()(request).data["next"]
        assert cursor
        request = factory.get("/", {"scope": "b", "limit": 1, "cursor": cursor}); force_authenticate(request, user=user)
        assert ChatListCreateView.as_view()(request).status_code == 400
        request = factory.get("/", {"scope": "c"}); force_authenticate(request, user=user)
        assert AvailabilityView.as_view()(request).status_code == 404
