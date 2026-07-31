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
def test_conversation_upsert_style_projection_excludes_participant_fields(serializer_domain):
    """The realtime `conversation_upsert` frame reuses `serialize_conversation_core`
    (not `serialize_conversation`) precisely because the latter's archived_at/muted/
    channel-preference fields are per-participant and would leak across a fanned-out
    frame — see docs/design/messaging-platform.md amendment. Assert the split holds."""
    from django_core_micha.messaging.serializers import serialize_conversation_core

    _, conversation, users = serializer_domain
    core = serialize_conversation_core(conversation)
    assert set(core).isdisjoint({"archived_at", "muted", "email_enabled", "push_enabled"})
    participant = ConversationParticipant.objects.get(conversation=conversation, user=users[0])
    full = serialize_conversation(conversation, participant)
    assert set(full) - set(core) == {"archived_at", "muted", "email_enabled", "push_enabled"}
