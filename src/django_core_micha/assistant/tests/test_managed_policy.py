import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.ai_policy.policy import apply_policy
from django_core_micha.assistant.models import AssistantChat

pytestmark = pytest.mark.django_db


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr("django_core_micha.ai_policy.resolver.find_spec", lambda package: object() if package == "openai" else None)
    rows = [
        {"source_id": "acct-a", "driver": "openai_api", "model": "same", "name": "A same",
         "efforts": ["low"], "billing": "pay_per_use", "default": True},
        {"source_id": "acct-b", "driver": "openai_api", "model": "same", "name": "B same",
         "efforts": ["low"], "billing": "pay_per_use"},
    ]
    settings = override_settings(
        DCM_AI_POLICY_ENABLED=True, DCM_AI_POLICY_APP_ID="bigler-consult",
        DCM_AI_POLICY_ENVIRONMENT="staging",
        DCM_AI_POLICY_SOURCES={
            "acct-a": {"label": "Account A", "credential_setting": "ACCOUNT_A_KEY"},
            "acct-b": {"label": "Account B", "credential_setting": "ACCOUNT_B_KEY"},
        },
        ACCOUNT_A_KEY="a-secret", ACCOUNT_B_KEY="b-secret", OPENAI_API_KEY="legacy",
        DCM_ASSISTANT_CATALOGUE=rows,
    )
    with settings:
        apply_policy({
            "app_id": "bigler-consult", "environment": "staging", "revision": 1,
            "allowed": {"acct-a": ["same"], "acct-b": ["same"]},
            "standard": {"source_id": "acct-a", "model_id": "same"},
        })
        yield


def test_catalogue_and_chat_keep_duplicate_model_sources_distinct(managed):
    from django_core_micha.assistant.views import CatalogueView, ChatListCreateView

    user = get_user_model().objects.create_user(username="managed")
    factory = APIRequestFactory()
    request = factory.get("/")
    force_authenticate(request, user=user)
    catalogue = CatalogueView.as_view()(request).data
    assert {entry["id"] for entry in catalogue["entries"]} == {"acct-a:same", "acct-b:same"}
    assert {entry["id"]: entry["source_label"] for entry in catalogue["entries"]} == {
        "acct-a:same": "Account A", "acct-b:same": "Account B",
    }
    assert catalogue["policy"] == {"revision": 1}
    assert catalogue["default"]["entry"] == "acct-a:same"
    request = factory.post("/", {"driver": "openai_api", "model": "same", "effort": "low", "source_id": "acct-b"},
                           format="json")
    force_authenticate(request, user=user)
    created = ChatListCreateView.as_view()(request).data
    assert created["source_id"] == "acct-b"
    assert created["selection"]["entry"] == "acct-b:same"


def test_revoked_saved_selection_uses_the_new_standard(managed):
    from django_core_micha.assistant.turns import _start

    user = get_user_model().objects.create_user(username="managed-revocation")
    chat = AssistantChat.objects.create(owner=user, selected_driver="openai_api",
        selected_model="same", selected_effort="low", selected_source_id="acct-b")
    apply_policy({
        "app_id": "bigler-consult", "environment": "staging", "revision": 2,
        "allowed": {"acct-a": ["same"]},
        "standard": {"source_id": "acct-a", "model_id": "same"},
    })
    turn = _start(chat.pk, user.pk, "hello")
    assert turn.source_id == "acct-a"
    chat.refresh_from_db()
    assert chat.selected_source_id == "acct-a"


def test_managed_completion_uses_selected_account_and_respects_local_restriction(managed, monkeypatch):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.base import DriverError

    calls = []

    class FakeDriver:
        async def stream(self, **kwargs):
            calls.append((kwargs["source_id"], kwargs["credential"]))
            yield {"type": "delta", "text": "synthetic result"}

    monkeypatch.setattr(completion, "_make_driver", lambda *_: FakeDriver())
    user = get_user_model().objects.create_user(username="managed-completion")
    complete = async_to_sync(completion.acomplete)
    complete(user, prompt="synthetic", entry="acct-b:same")
    complete(user, prompt="synthetic")
    with override_settings(DCM_ASSISTANT_ENTRY_ALLOWED=lambda _user, row: row["source_id"] != "acct-b"):
        with pytest.raises(DriverError, match="invalid_selection"):
            complete(user, prompt="synthetic", entry="acct-b:same")
    assert calls == [("acct-b", "b-secret"), ("acct-a", "a-secret")]


def test_revocation_before_provider_dispatch_prevents_queued_source_call(managed, monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from django_core_micha.assistant import turns
    from django_core_micha.assistant.drivers.base import DriverError

    user = get_user_model().objects.create_user(username="managed-queued")
    chat = AssistantChat.objects.create(owner=user, selected_driver="openai_api", selected_model="same",
                                        selected_effort="low", selected_source_id="acct-b")

    def revoke(*_args, **_kwargs):
        apply_policy({"app_id": "bigler-consult", "environment": "staging", "revision": 2,
                      "allowed": {"acct-a": ["same"]},
                      "standard": {"source_id": "acct-a", "model_id": "same"}})
        return None

    make_driver = Mock()
    monkeypatch.setattr(turns, "build_turn_context", revoke)
    monkeypatch.setattr(turns, "_make_driver", make_driver)
    with pytest.raises(DriverError, match="unavailable"):
        async_to_sync(turns.run_turn)(chat, user, "synthetic", AsyncMock())
    make_driver.assert_not_called()
    assert chat.turns.get().error_code == "unavailable"
