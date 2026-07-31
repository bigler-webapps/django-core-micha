import uuid

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from django_core_micha.messaging.crypto import register_messaging_app
from django_core_micha.messaging.models import (Conversation, ConversationParticipant,
                                                 Message, MessagingApp, MessagingScope,
                                                 Poll, PollOption)
from django_core_micha.messaging.policy import MembershipSnapshot, register_messaging_policy, unregister_messaging_policy


class Policy:
    rights = frozenset({"open_group", "read_receipt_detail"})
    def can_open_direct(self, **kwargs): return True
    def can_view_conversation(self, **kwargs): return kwargs["actor"].username != "outsider"
    def can_post(self, **kwargs): return kwargs["actor"].username != "viewer"
    def moderation_rights(self, **kwargs): return self.rights
    def resolve_recipients(self, **kwargs): return []
    def provision_membership(self, **kwargs): return MembershipSnapshot([])
    def validate_scope(self, **kwargs): return {}


@pytest.fixture
def api_domain(db):
    key = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"api-app": [key]}):
        register_messaging_app("api-app")
        app = MessagingApp.objects.create(app_key="api-app", keyset_id="test")
        scope = MessagingScope.objects.create(app=app, kind="global")
        users = {name: get_user_model().objects.create_user(username=name) for name in ("author", "viewer", "outsider")}
        conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
        for user in users.values():
            if user.username != "outsider": ConversationParticipant.objects.create(conversation=conversation, user=user)
        policy = Policy(); register_messaging_policy(app.app_key, policy)
        yield app, scope, conversation, users
    unregister_messaging_policy("api-app")


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_message_post_retries_once_with_idempotency_key(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    request_id = str(uuid.uuid4())
    url = f"/messaging/conversations/{conversation.id}/messages/"
    first = client.post(url, {"body": "sensitive", "client_request_id": request_id}, format="json", HTTP_IDEMPOTENCY_KEY=request_id)
    second = client.post(url, {"body": "different", "client_request_id": request_id}, format="json", HTTP_IDEMPOTENCY_KEY=request_id)
    assert first.status_code == 201 and second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert second.data["body"] == "sensitive"


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_non_member_is_404_and_capability_failure_is_403(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); url = f"/messaging/conversations/{conversation.id}/messages/"
    client.force_authenticate(users["outsider"])
    assert client.get(url).status_code == 404
    client.force_authenticate(users["viewer"])
    assert client.post(url, {"body": "no"}, format="json").status_code == 403


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_cursor_is_signed_and_bad_cursor_is_400(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    url = f"/messaging/conversations/{conversation.id}/messages/"
    for number in range(2): assert client.post(url, {"body": str(number)}, format="json").status_code == 201
    response = client.get(f"{url}?limit=1")
    assert response.status_code == 200 and response.data["next_cursor"]
    assert client.get(f"{url}?cursor=not-a-signed-cursor").status_code == 400


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_direct_scope_resolves_tenant_without_reading_app_key(api_domain):
    app, scope, _, users = api_domain
    foreign = MessagingApp.objects.create(app_key="foreign-app", keyset_id="test")
    client = APIClient(); client.force_authenticate(users["author"])
    response = client.post("/messaging/conversations/direct/", {
        "target_user_id": str(users["viewer"].pk), "scope": str(scope.pk), "app_key": foreign.app_key,
    }, format="json")
    assert response.status_code == 201
    assert Conversation.objects.get(pk=response.data["id"]).app_id == app.id


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_deleted_message_rest_payload_is_blank(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    url = f"/messaging/conversations/{conversation.id}/messages/"
    created = client.post(url, {"body": "secret", "title": "heading", "link_target": "https://example.test"}, format="json")
    assert client.delete(f"/messaging/messages/{created.data['id']}/").status_code == 204
    returned = client.get(f"/messaging/messages/{created.data['id']}/")
    assert returned.status_code == 200
    assert returned.data["body"] is None and returned.data["title"] is None and returned.data["link_target"] is None


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_thread_pagination_and_poll_permissions(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    messages_url = f"/messaging/conversations/{conversation.id}/messages/"
    root = client.post(messages_url, {"body": "root"}, format="json").data
    for value in ("one", "two"):
        assert client.post(messages_url, {"body": value, "reply_to": root["id"]}, format="json").status_code == 201
    thread_url = f"/messaging/messages/{root['id']}/thread/"
    first = client.get(f"{thread_url}?limit=1")
    assert first.status_code == 200 and len(first.data["results"]) == 1 and first.data["next_cursor"]
    assert len(client.get(f"{thread_url}?cursor={first.data['next_cursor']}").data["results"]) == 1
    poll = client.post(f"/messaging/conversations/{conversation.id}/polls/", {"question": "q", "options": ["a", "b"]}, format="json")
    assert poll.status_code == 201
    poll_id = poll.data["id"]
    option = PollOption.objects.filter(poll_id=poll_id).first()
    client.force_authenticate(users["viewer"])
    # Voting only requires current participation (design §REST: "participant/open poll"),
    # not can_post/moderation rights — unlike posting, which the Policy fixture denies
    # for "viewer" (see test_non_member_is_404_and_capability_failure_is_403).
    assert client.post(f"/messaging/polls/{poll_id}/vote/", {"option_ids": [str(option.id)]}, format="json").status_code == 204
    # Closing requires being the poll's creator or holding edit_any/delete_any; "viewer"
    # has neither (Policy.rights only grants open_group/read_receipt_detail).
    assert client.post(f"/messaging/polls/{poll_id}/close/", {}, format="json").status_code == 403
    client.force_authenticate(users["author"])
    assert client.post(f"/messaging/polls/{poll_id}/close/", {}, format="json").status_code == 200


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_direct_read_status_never_has_recipient_detail(api_domain):
    app, scope, _, users = api_domain
    direct = Conversation.objects.create(app=app, scope=scope, kind="direct", user_low=users["author"], user_high=users["viewer"])
    for user in (users["author"], users["viewer"]): ConversationParticipant.objects.create(conversation=direct, user=user)
    message = Message.objects.create(conversation=direct, sender=users["author"], body="private")
    client = APIClient(); client.force_authenticate(users["author"])
    response = client.get(f"/messaging/messages/{message.id}/read-status/")
    assert response.status_code == 200 and "recipient_detail" not in response.data


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_config_get_is_viewer_gated_and_patch_requires_manage_config(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient()
    config_url = f"/messaging/conversations/{conversation.id}/config/"

    client.force_authenticate(users["outsider"])
    assert client.get(config_url).status_code == 404

    client.force_authenticate(users["viewer"])
    assert client.get(config_url).status_code == 200
    # Policy fixture's default rights (open_group, read_receipt_detail) do not include
    # manage_config, so even a live viewer/participant is denied the PATCH.
    assert client.patch(config_url, {"config": {"everyone_can_post": False}}, format="json").status_code == 403

    from django_core_micha.messaging.policy import register_messaging_policy, unregister_messaging_policy

    class ManagerPolicy(type(Policy())):
        rights = frozenset({"manage_config"})

    unregister_messaging_policy("api-app")
    register_messaging_policy("api-app", ManagerPolicy())
    try:
        response = client.patch(config_url, {"config": {"everyone_can_post": False}}, format="json")
        assert response.status_code == 200 and response.data["everyone_can_post"] is False
    finally:
        unregister_messaging_policy("api-app")
        register_messaging_policy("api-app", Policy())


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_reactions_archive_and_preferences_require_current_participant(api_domain):
    _, _, conversation, users = api_domain
    message = Message.objects.create(conversation=conversation, sender=users["author"], body="reactable")
    client = APIClient()

    client.force_authenticate(users["outsider"])
    assert client.post(f"/messaging/messages/{message.id}/reactions/", {"emoji": "\U0001F44D"}, format="json").status_code == 404
    assert client.post(f"/messaging/conversations/{conversation.id}/archive/", {}, format="json").status_code == 404
    assert client.post(f"/messaging/conversations/{conversation.id}/preferences/", {"muted": True}, format="json").status_code == 404

    client.force_authenticate(users["viewer"])
    add = client.post(f"/messaging/messages/{message.id}/reactions/", {"emoji": "\U0001F44D"}, format="json")
    assert add.status_code == 200 and add.data["reactions"] == [{"emoji": "\U0001F44D", "count": 1}]
    assert client.delete(f"/messaging/messages/{message.id}/reactions/%F0%9F%91%8D/").status_code == 204
    assert client.post(f"/messaging/conversations/{conversation.id}/archive/", {}, format="json").status_code == 204
    prefs = client.post(f"/messaging/conversations/{conversation.id}/preferences/", {"muted": True}, format="json")
    assert prefs.status_code == 200 and prefs.data["muted"] is True


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_unread_count_reflects_only_the_caller(api_domain):
    _, _, conversation, users = api_domain
    Message.objects.create(conversation=conversation, sender=users["author"], body="unread for viewer")
    client = APIClient(); client.force_authenticate(users["viewer"])
    response = client.get("/messaging/unread-count/")
    assert response.status_code == 200 and response.data["unread_count"] >= 1
