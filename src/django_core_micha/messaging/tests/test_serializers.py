import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.messaging.crypto import register_messaging_app
from django_core_micha.messaging.models import (Conversation, ConversationParticipant, Message,
                                                 MessagingApp, MessagingScope, Poll, PollOption, PollVote)
from django_core_micha.messaging.serializers import serialize_conversation, serialize_message, serialize_poll


@pytest.fixture
def serializer_domain(db):
    key = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"serializer-app": [key]}):
        register_messaging_app("serializer-app")
        app = MessagingApp.objects.create(app_key="serializer-app", keyset_id="test")
        scope = MessagingScope.objects.create(app=app, kind="global")
        conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
        users = [get_user_model().objects.create_user(username=name) for name in ("alice", "bob")]
        for user in users:
            ConversationParticipant.objects.create(conversation=conversation, user=user)
        yield app, conversation, users


def _poll_message(conversation, creator, question="q", options=("a", "b")):
    message = Message.objects.create(conversation=conversation, sender=creator, kind="poll")
    poll = Poll.objects.create(message=message, question=question, created_by=creator)
    rows = [PollOption(poll=poll, text=text, order=index) for index, text in enumerate(options)]
    PollOption.objects.bulk_create(rows)
    return message, poll


@pytest.mark.django_db
def test_serialize_poll_decrypts_and_never_carries_voted_option_ids(serializer_domain):
    _, conversation, users = serializer_domain
    _, poll = _poll_message(conversation, users[0], question="Coffee or tea?", options=("Coffee", "Tea"))
    option = poll.options.first()
    PollVote.objects.create(option=option, user=users[1])

    projection = serialize_poll(poll)

    assert projection["question"] == "Coffee or tea?"
    assert [o["text"] for o in projection["options"]] == ["Coffee", "Tea"]
    assert projection["options"][0]["vote_count"] == 1
    assert projection["options"][0]["voters"] == [users[1].pk]
    assert projection["options"][1]["vote_count"] == 0 and projection["options"][1]["voters"] == []
    assert projection["created_by_id"] == users[0].pk
    assert "voted_option_ids" not in projection


@pytest.mark.django_db
def test_serialize_message_embeds_poll_only_for_poll_kind(serializer_domain):
    _, conversation, users = serializer_domain
    poll_message, _ = _poll_message(conversation, users[0])
    chat_message = Message.objects.create(conversation=conversation, sender=users[0], kind="chat", body="hi")

    assert "poll" in serialize_message(poll_message)
    assert "poll" not in serialize_message(chat_message)


@pytest.mark.django_db
def test_last_message_is_none_bounded_and_reflects_special_kinds(serializer_domain):
    _, conversation, users = serializer_domain
    participant = ConversationParticipant.objects.get(conversation=conversation, user=users[0])
    assert serialize_conversation(conversation, participant)["last_message"] is None

    long_body = "x" * 500
    Message.objects.create(conversation=conversation, sender=users[0], body=long_body)
    excerpt = serialize_conversation(conversation, participant)["last_message"]["excerpt"]
    assert len(excerpt) <= 140 and excerpt == long_body[:140]

    poll_message, poll = _poll_message(conversation, users[0], question="Pick one")
    last = serialize_conversation(conversation, participant)["last_message"]
    assert last["id"] == str(poll_message.id) and last["kind"] == "poll" and last["excerpt"] == "Pick one"

    deleted = Message.objects.create(conversation=conversation, sender=users[0], body="temp")
    deleted.deleted_at = deleted.created_at
    deleted.body = deleted.title = deleted.link_target = None
    deleted.save(update_fields=["deleted_at", "body", "title", "link_target"])
    last = serialize_conversation(conversation, participant)["last_message"]
    assert last["id"] == str(deleted.id) and last["excerpt"] == ""


@pytest.mark.django_db
def test_reply_count_and_last_reply_at_count_soft_deleted_replies(serializer_domain):
    _, conversation, users = serializer_domain
    root = Message.objects.create(conversation=conversation, sender=users[0], body="root")
    assert serialize_message(root)["reply_count"] == 0
    assert serialize_message(root)["last_reply_at"] is None

    first = Message.objects.create(conversation=conversation, sender=users[1], body="one", reply_to=root)
    second = Message.objects.create(conversation=conversation, sender=users[1], body="two", reply_to=root)
    data = serialize_message(Message.objects.get(pk=root.pk))
    assert data["reply_count"] == 2 and data["last_reply_at"] == second.created_at

    # A soft-deleted reply keeps its row (rendered as a tombstone) and must still count —
    # excluding it would undercount against what the thread actually displays.
    first.deleted_at = first.created_at
    first.body = first.title = first.link_target = None
    first.save(update_fields=["deleted_at", "body", "title", "link_target"])
    data = serialize_message(Message.objects.get(pk=root.pk))
    assert data["reply_count"] == 2 and data["last_reply_at"] == second.created_at


@pytest.mark.django_db
def test_external_key_is_null_unless_managed_or_broadcast(serializer_domain):
    app, conversation, users = serializer_domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    participant = ConversationParticipant.objects.get(conversation=conversation, user=users[0])
    assert conversation.kind == "group"
    assert serialize_conversation(conversation, participant)["external_key"] is None

    def _external_key(kind, **extra):
        row = Conversation.objects.create(app=app, scope=extra.pop("scope", scope), kind=kind, **extra)
        ConversationParticipant.objects.create(conversation=row, user=users[0])
        return serialize_conversation(row, ConversationParticipant.objects.get(conversation=row, user=users[0]))["external_key"]

    assert _external_key("managed", external_key="event_all") == "event_all"
    assert _external_key("broadcast", external_key="announcements") == "announcements"
    assert _external_key("direct", user_low=users[0], user_high=users[1]) is None

    from django.contrib.contenttypes.models import ContentType
    object_scope = MessagingScope.objects.create(app=app, kind="object", content_type=ContentType.objects.get_for_model(get_user_model()), object_id=str(users[0].pk))
    assert _external_key("object_thread", scope=object_scope) is None


@pytest.mark.django_db
def test_conversation_upsert_style_projection_excludes_participant_fields(serializer_domain):
    """The realtime `conversation_upsert` frame reuses `serialize_conversation_core`
    (not `serialize_conversation`) precisely because the latter's archived_at/muted/
    channel-preference/other_user_id fields are per-participant (`other_user_id` is
    the counterpart RELATIVE TO the viewer, so it differs per recipient — the same
    reason it must never leak into the shared fanned-out frame) and would leak
    across a fanned-out frame — see docs/design/messaging-platform.md amendment.
    Assert the split holds."""
    from django_core_micha.messaging.serializers import serialize_conversation_core

    _, conversation, users = serializer_domain
    core = serialize_conversation_core(conversation)
    assert set(core).isdisjoint({"archived_at", "muted", "email_enabled", "push_enabled", "other_user_id"})
    participant = ConversationParticipant.objects.get(conversation=conversation, user=users[0])
    full = serialize_conversation(conversation, participant)
    assert set(full) - set(core) == {"archived_at", "muted", "email_enabled", "push_enabled", "other_user_id"}


@pytest.mark.django_db
def test_serialize_conversation_resolves_other_user_id_relative_to_the_viewer(serializer_domain):
    """`other_user_id` is a bare id, never a resolved name — dcm has no notion of a
    consuming app's User model shape beyond its pk (the same reason `sender_id` on
    a message is never accompanied by a display name). It must also be genuinely
    relative to whichever participant is asking, not a fixed low/high pick."""
    app, _group_conversation, users = serializer_domain
    alice, bob = users
    low, high = sorted((alice, bob), key=lambda user: str(user.pk))
    scope = MessagingScope.objects.get(app=app, kind="global")
    direct = Conversation.objects.create(app=app, scope=scope, kind=Conversation.Kind.DIRECT, user_low=low, user_high=high)
    alice_participant = ConversationParticipant.objects.create(conversation=direct, user=alice)
    bob_participant = ConversationParticipant.objects.create(conversation=direct, user=bob)

    assert serialize_conversation(direct, alice_participant)["other_user_id"] == bob.pk
    assert serialize_conversation(direct, bob_participant)["other_user_id"] == alice.pk


@pytest.mark.django_db
def test_serialize_conversation_other_user_id_is_none_for_non_direct_kinds(serializer_domain):
    _, group_conversation, users = serializer_domain
    participant = ConversationParticipant.objects.get(conversation=group_conversation, user=users[0])
    assert serialize_conversation(group_conversation, participant)["other_user_id"] is None
