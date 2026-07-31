import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured

from django_core_micha.messaging.crypto import decrypt_text, register_messaging_app
from django_core_micha.messaging.models import (
    Conversation,
    Message,
    MessageAttachment,
    MessagingApp,
    MessagingScope,
    Poll,
    PollOption,
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="message-author", password="password")


@pytest.fixture
def registered_app(db):
    register_messaging_app("test-app")
    return MessagingApp.objects.create(app_key="test-app", keyset_id="test-keyset")


@pytest.fixture
def conversation(registered_app):
    scope = MessagingScope.objects.create(app=registered_app, kind=MessagingScope.Kind.GLOBAL)
    return Conversation.objects.create(app=registered_app, scope=scope, kind=Conversation.Kind.GROUP)


@pytest.mark.django_db
def test_message_text_is_encrypted_at_rest_and_round_trips(conversation, user):
    message = Message.objects.create(conversation=conversation, sender=user, body="private message")

    stored_value = Message.objects.values_list("body", flat=True).get(pk=message.pk)

    assert stored_value != "private message"
    assert "private message" not in stored_value
    assert decrypt_text(app_key="test-app", value=stored_value) == "private message"


@pytest.mark.django_db
def test_message_save_fails_closed_for_unregistered_app(user):
    other_app = MessagingApp.objects.create(app_key="unregistered-app", keyset_id="other-keyset")
    scope = MessagingScope.objects.create(app=other_app, kind=MessagingScope.Kind.GLOBAL)
    conversation = Conversation.objects.create(app=other_app, scope=scope, kind=Conversation.Kind.GROUP)

    with pytest.raises(ImproperlyConfigured):
        Message.objects.create(conversation=conversation, sender=user, body="never persisted")


@pytest.mark.django_db
def test_attachment_and_poll_accessor_chains_encrypt_under_owning_app(conversation, user):
    message = Message.objects.create(conversation=conversation, sender=user, kind=Message.Kind.POLL)

    attachment = MessageAttachment.objects.create(
        message=message,
        blob_key="blob-key-value",
        filename="receipt.pdf",
        content_type="application/pdf",
        byte_size=1024,
        sha256="0" * 64,
    )
    poll = Poll.objects.create(message=message, question="Pineapple on pizza?")
    option = PollOption.objects.create(poll=poll, text="Yes")

    stored_blob_key = MessageAttachment.objects.values_list("blob_key", flat=True).get(pk=attachment.pk)
    stored_question = Poll.objects.values_list("question", flat=True).get(pk=poll.pk)
    stored_option_text = PollOption.objects.values_list("text", flat=True).get(pk=option.pk)

    assert decrypt_text(app_key="test-app", value=stored_blob_key) == "blob-key-value"
    assert decrypt_text(app_key="test-app", value=stored_question) == "Pineapple on pizza?"
    assert decrypt_text(app_key="test-app", value=stored_option_text) == "Yes"
