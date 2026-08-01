import uuid

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from django_core_micha.messaging.crypto import register_messaging_app
from django_core_micha.messaging.models import (Conversation, ConversationParticipant,
                                                 Message, MessagingApp, MessagingScope,
                                                 Poll, PollOption)
from django_core_micha.messaging.policy import (MembershipSnapshot, get_messaging_policy,
                                                register_messaging_policy, unregister_messaging_policy)


class Policy:
    rights = frozenset({"open_group", "read_receipt_detail"})
    def can_open_direct(self, **kwargs): return True
    def can_view_conversation(self, **kwargs): return kwargs["actor"].username != "outsider"
    def can_post(self, **kwargs): return kwargs["actor"].username != "viewer"
    def moderation_rights(self, **kwargs): return self.rights
    def resolve_recipients(self, **kwargs): return []
    snapshot = MembershipSnapshot([])
    def provision_membership(self, **kwargs): return self.snapshot
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
def test_provider_member_added_on_group_creation_can_see_conversation(api_domain):
    app, scope, _, users = api_domain
    policy = get_messaging_policy(app.app_key)
    policy.snapshot = MembershipSnapshot([users["viewer"]], remove_absent=True)
    client = APIClient(); client.force_authenticate(users["author"])

    created = client.post("/messaging/conversations/group/", {
        "scope": str(scope.pk), "external_key": "group:42", "participant_ids": [],
    }, format="json")

    assert created.status_code == 201
    client.force_authenticate(users["viewer"])
    listed = client.get("/messaging/conversations/")
    assert listed.status_code == 200
    assert str(created.data["id"]) in {row["id"] for row in listed.data["results"]}


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_authorized_actor_joins_keyed_group_on_reopen(api_domain):
    app, scope, _, users = api_domain
    policy = get_messaging_policy(app.app_key)
    policy.snapshot = MembershipSnapshot([])
    client = APIClient(); client.force_authenticate(users["author"])
    payload = {"scope": str(scope.pk), "external_key": "group:private", "participant_ids": []}
    created = client.post("/messaging/conversations/group/", payload, format="json")
    assert created.status_code == 201

    client.force_authenticate(users["outsider"])
    reopened = client.post("/messaging/conversations/group/", payload, format="json")
    assert reopened.status_code == 201
    assert reopened.data["id"] == created.data["id"]
    assert ConversationParticipant.objects.filter(conversation_id=reopened.data["id"], user=users["outsider"], removed_at__isnull=True).exists()


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
        "target_user_id": str(users["outsider"].pk), "scope": str(scope.pk), "app_key": foreign.app_key,
    }, format="json")
    assert response.status_code == 201
    assert Conversation.objects.get(pk=response.data["id"]).app_id == app.id


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_scoped_direct_first_contact_consults_permitting_policy(api_domain, monkeypatch):
    app, scope, _, users = api_domain
    target = users["outsider"]
    assert not ConversationParticipant.objects.filter(conversation__app=app, user=target).exists()
    calls = []

    def can_open_direct(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(get_messaging_policy(app.app_key), "can_open_direct", can_open_direct)
    client = APIClient(); client.force_authenticate(users["author"])
    response = client.post("/messaging/conversations/direct/", {
        "target_user_id": str(target.pk), "scope": str(scope.pk),
    }, format="json")

    assert response.status_code == 201
    assert calls == [{"actor": users["author"], "target": target, "scope": scope}]
    assert ConversationParticipant.objects.filter(conversation_id=response.data["id"], user=target).exists()


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_scoped_direct_first_contact_denied_by_policy(api_domain, monkeypatch):
    app, scope, _, users = api_domain
    target = users["outsider"]
    assert not ConversationParticipant.objects.filter(conversation__app=app, user=target).exists()
    calls = []

    def can_open_direct(**kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(get_messaging_policy(app.app_key), "can_open_direct", can_open_direct)
    client = APIClient(); client.force_authenticate(users["author"])
    response = client.post("/messaging/conversations/direct/", {
        "target_user_id": str(target.pk), "scope": str(scope.pk),
    }, format="json")

    assert response.status_code == 403
    assert len(calls) == 1
    assert not Conversation.objects.filter(app=app, kind=Conversation.Kind.DIRECT).exists()


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_direct_self_dm_is_rejected(api_domain):
    _, scope, _, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    response = client.post("/messaging/conversations/direct/", {
        "target_user_id": str(users["author"].pk), "scope": str(scope.pk),
    }, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_global_direct_fails_closed_without_exactly_one_active_app(api_domain):
    app, _, _, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])

    app.active = False; app.save(update_fields=["active"])
    zero = client.post("/messaging/conversations/direct/", {"target_user_id": str(users["viewer"].pk)}, format="json")
    assert zero.status_code == 400

    app.active = True; app.save(update_fields=["active"])
    MessagingApp.objects.create(app_key="another-app", keyset_id="test")
    many = client.post("/messaging/conversations/direct/", {"target_user_id": str(users["viewer"].pk)}, format="json")
    assert many.status_code == 400


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
    # MSG-2c: vote/close now return the same poll projection as create — no more 204.
    voted = client.post(f"/messaging/polls/{poll_id}/vote/", {"option_ids": [str(option.id)]}, format="json")
    assert voted.status_code == 200
    assert voted.data["id"] == poll_id and voted.data["voted_option_ids"] == [str(option.id)]
    assert "voted_option_ids" not in {**voted.data["options"][0]}
    # Closing requires being the poll's creator or holding edit_any/delete_any; "viewer"
    # has neither (Policy.rights only grants open_group/read_receipt_detail).
    assert client.post(f"/messaging/polls/{poll_id}/close/", {}, format="json").status_code == 403
    client.force_authenticate(users["author"])
    closed = client.post(f"/messaging/polls/{poll_id}/close/", {}, format="json")
    assert closed.status_code == 200
    assert closed.data["closed_at"] is not None
    # The poll's own creator ("author") never voted — their voted_option_ids is empty,
    # while "viewer"'s vote above is unaffected: per-viewer, not shared state.
    assert closed.data["voted_option_ids"] == []


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_poll_voted_option_ids_is_per_viewer_and_embedded_in_message(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    poll = client.post(f"/messaging/conversations/{conversation.id}/polls/", {"question": "q", "options": ["a", "b"]}, format="json")
    poll_id = poll.data["id"]
    # A freshly created poll has no votes yet from anyone.
    assert poll.data["voted_option_ids"] == []
    options = {option["text"]: option["id"] for option in poll.data["options"]}
    client.post(f"/messaging/polls/{poll_id}/vote/", {"option_ids": [options["a"]]}, format="json")
    client.force_authenticate(users["viewer"])
    client.post(f"/messaging/polls/{poll_id}/vote/", {"option_ids": [options["b"]]}, format="json")

    message_id = Poll.objects.get(pk=poll_id).message_id

    author_view = client.__class__(); author_view.force_authenticate(users["author"])
    author_message = author_view.get(f"/messaging/messages/{message_id}/")
    viewer_message = client.get(f"/messaging/messages/{message_id}/")
    assert author_message.data["poll"]["id"] == poll_id and viewer_message.data["poll"]["id"] == poll_id
    # serialize_message's embedded poll is the viewer-independent core — it must never
    # carry voted_option_ids, for either viewer, even though each voted differently above.
    assert "voted_option_ids" not in author_message.data["poll"]
    assert "voted_option_ids" not in viewer_message.data["poll"]
    # The core payload itself (aggregate vote_count/voters) is byte-identical for both viewers.
    assert author_message.data["poll"] == viewer_message.data["poll"]
    voters_by_option = {o["text"]: set(o["voters"]) for o in author_message.data["poll"]["options"]}
    assert voters_by_option["a"] == {users["author"].pk} and voters_by_option["b"] == {users["viewer"].pk}


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_poll_voters_are_visible_in_direct_conversations_too(api_domain):
    app, scope, _, users = api_domain
    direct = Conversation.objects.create(app=app, scope=scope, kind="direct", user_low=users["author"], user_high=users["viewer"])
    for user in (users["author"], users["viewer"]):
        ConversationParticipant.objects.create(conversation=direct, user=user)
    client = APIClient(); client.force_authenticate(users["author"])
    poll = client.post(f"/messaging/conversations/{direct.id}/polls/", {"question": "dm poll", "options": ["x", "y"]}, format="json")
    option_id = poll.data["options"][0]["id"]
    client.post(f"/messaging/polls/{poll.data['id']}/vote/", {"option_ids": [option_id]}, format="json")
    refreshed = client.get(f"/messaging/messages/{Poll.objects.get(pk=poll.data['id']).message_id}/")
    # No DM carve-out for poll voters (design amendment, MSG-2c) — user ids, never names.
    assert refreshed.data["poll"]["options"][0]["voters"] == [users["author"].pk]


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


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_thread_last_read_at_is_per_viewer_and_null_without_a_receipt(api_domain):
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    messages_url = f"/messaging/conversations/{conversation.id}/messages/"
    root = client.post(messages_url, {"body": "root"}, format="json").data
    client.post(messages_url, {"body": "a reply", "reply_to": root["id"]}, format="json")

    # Nobody has marked the thread read yet — null for every viewer.
    fresh = client.get(f"/messaging/messages/{root['id']}/")
    assert fresh.data["thread_last_read_at"] is None

    client.post(f"/messaging/messages/{root['id']}/thread/read/", {}, format="json")
    author_view = client.get(f"/messaging/messages/{root['id']}/")
    assert author_view.data["thread_last_read_at"] is not None

    client.force_authenticate(users["viewer"])
    viewer_view = client.get(f"/messaging/messages/{root['id']}/")
    assert viewer_view.data["thread_last_read_at"] is None  # viewer never marked it read

    # Also present, and bulk-correct, on the conversation message-list endpoint (not
    # just single-message GET) — the same viewer, same root, same answer either way.
    listed = client.get(messages_url).data["results"]
    listed_root = next(m for m in listed if m["id"] == root["id"])
    assert listed_root["thread_last_read_at"] is None


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_thread_last_read_at_is_absent_from_realtime_frames(api_domain, monkeypatch, django_capture_on_commit_callbacks):
    """The load-bearing viewer-independence rule from MSG-2c extends to this new
    per-viewer field: thread_last_read_at must never ride the message/message_edited
    realtime frame, only the REST response, since a frame is fanned out identically
    to every recipient. (transaction.on_commit callbacks never fire under pytest-django's
    default atomic-wrapped test — django_capture_on_commit_callbacks(execute=True) is
    what actually runs them; see test_services.py for the same pattern.)"""
    _, _, conversation, users = api_domain
    sent = []
    monkeypatch.setattr("django_core_micha.messaging.realtime.push_to_users", lambda users, frame: sent.append(frame))
    client = APIClient(); client.force_authenticate(users["author"])
    messages_url = f"/messaging/conversations/{conversation.id}/messages/"

    with django_capture_on_commit_callbacks(execute=True):
        root_id = client.post(messages_url, {"body": "root"}, format="json").data["id"]
    with django_capture_on_commit_callbacks(execute=True):
        client.post(f"/messaging/messages/{root_id}/thread/read/", {}, format="json")

    message_frames = [f for f in sent if f["type"] in {"message", "message_edited"}]
    assert message_frames  # sanity: the send above did fan out
    for frame in message_frames:
        assert "thread_last_read_at" not in frame["message"]
        assert "reply_count" in frame["message"] and "last_reply_at" in frame["message"]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_message_list_query_count_is_bounded_regardless_of_page_size(api_domain):
    """reply_count/last_reply_at/thread_last_read_at must be annotated/bulk-fetched,
    not queried per row — a page of 5 root messages (each with a reply) must not cost
    more queries than a page of 2."""
    _, _, conversation, users = api_domain
    client = APIClient(); client.force_authenticate(users["author"])
    messages_url = f"/messaging/conversations/{conversation.id}/messages/"

    def _seed(count):
        for _ in range(count):
            root = client.post(messages_url, {"body": "root"}, format="json").data
            client.post(messages_url, {"body": "reply", "reply_to": root["id"]}, format="json")

    _seed(2)
    with CaptureQueriesContext(connection) as small:
        response = client.get(messages_url)
    assert response.status_code == 200 and len(response.data["results"]) == 2

    _seed(3)  # 5 root messages total now, still one page (default limit 50)
    with CaptureQueriesContext(connection) as large:
        response = client.get(messages_url)
    assert response.status_code == 200 and len(response.data["results"]) == 5

    assert len(large.captured_queries) == len(small.captured_queries)
