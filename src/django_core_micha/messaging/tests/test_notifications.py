from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from django_core_micha.messaging.crypto import register_messaging_app
from django_core_micha.messaging.models import Conversation, ConversationParticipant, MessageAttachment, MessagingApp, MessagingScope
from django_core_micha.messaging.notifications import (
    messaging_notification_type_key,
    notify_message,
    register_messaging_notification_type,
)
from django_core_micha.messaging.policy import MembershipSnapshot, register_messaging_policy, unregister_messaging_policy
from django_core_micha.messaging.services import send_message
from django_core_micha.notifications.dispatch import _push_preview_enabled
from django_core_micha.notifications.models import Notification, NotificationPreference
from django_core_micha.notifications.types import get_notification_type


class TestPolicy:
    recipients = []
    def can_open_direct(self, **kwargs): return True
    def can_view_conversation(self, **kwargs): return True
    def can_post(self, **kwargs): return True
    def moderation_rights(self, **kwargs): return frozenset()
    def resolve_recipients(self, **kwargs): return self.recipients
    def provision_membership(self, **kwargs): return MembershipSnapshot([])
    def validate_scope(self, **kwargs): return {}


@pytest.fixture
def domain(db):
    key = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"notify-app": [key]}):
        register_messaging_app("notify-app")
        app = MessagingApp.objects.create(app_key="notify-app", keyset_id="test")
        scope = MessagingScope.objects.create(app=app, kind="global")
        conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
        users = [get_user_model().objects.create_user(username=name) for name in ("sender", "recipient")]
        for user in users:
            ConversationParticipant.objects.create(conversation=conversation, user=user)
        policy = TestPolicy(); policy.recipients = users
        register_messaging_policy("notify-app", policy)
        yield app, conversation, users
    unregister_messaging_policy("notify-app")


@pytest.mark.django_db
def test_notify_message_is_a_noop_for_an_app_that_never_registered(domain):
    _, conversation, users = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="hello")
    assert notify_message(message=message, recipients=[users[1]]) is None
    assert not Notification.objects.exists()


@pytest.mark.django_db
def test_notify_message_recipe_matches_jg_precedent(domain):
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    notification_type = get_notification_type(messaging_notification_type_key(app.app_key))
    assert notification_type.eligible_channels == ["email", "push"]
    assert notification_type.default_channels == ["email", "push"]
    assert "chip" not in notification_type.eligible_channels
    assert notification_type.feed_visible is False

    message, _ = send_message(actor=users[0], conversation=conversation, body="sensitive body", title="sensitive title")
    before = timezone.now()
    notification = notify_message(message=message, recipients=[users[1]])
    after = timezone.now()

    assert notification is not None
    # content carries only i18n keys/link/message_id — never the plaintext body/title.
    assert notification.content["params"] == {"message_id": str(message.id)}
    assert "sensitive body" not in str(notification.content)
    assert "sensitive title" not in str(notification.content)
    # 30-day TTL, wired to the exact field prune_notifications already queries on.
    assert before + timedelta(days=30) <= notification.expires_at <= after + timedelta(days=30)


@pytest.mark.django_db
def test_send_message_on_commit_delivers_notification_end_to_end(domain, django_capture_on_commit_callbacks, monkeypatch):
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    monkeypatch.setattr("django_core_micha.messaging.realtime.push_to_users", lambda users, frame: None)

    with django_capture_on_commit_callbacks(execute=True):
        send_message(actor=users[0], conversation=conversation, body="hello")

    notification = Notification.objects.get(notification_type=messaging_notification_type_key(app.app_key))
    assert list(notification.recipients.values_list("user_id", flat=True)) == [users[1].pk]


@pytest.mark.django_db
def test_notify_message_delivery_failure_does_not_roll_back_the_message(domain, django_capture_on_commit_callbacks, monkeypatch):
    """A notify()/dispatch exception must never take the durable message down with it —
    _notify_message in services.py deliberately catches and logs instead of propagating."""
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)

    def boom(**kwargs):
        raise RuntimeError("simulated dispatch outage")

    monkeypatch.setattr("django_core_micha.messaging.notifications.notify", boom)

    with django_capture_on_commit_callbacks(execute=True):
        message, created = send_message(actor=users[0], conversation=conversation, body="still saved")

    assert created
    from django_core_micha.messaging.models import Message
    assert Message.objects.filter(pk=message.pk).exists()


def _capture_push(monkeypatch):
    sent = []
    monkeypatch.setattr("django_core_micha.notifications.dispatch._send_push", lambda **kwargs: sent.append(kwargs))
    return sent


@pytest.mark.django_db
def test_notify_message_push_title_is_sender_name_and_body_is_the_message_text(domain, monkeypatch):
    # Regression test for MSG-13 (the raw-key report): with the pre-fix code this would
    # have rendered both title and body as the literal string "messaging.new_message".
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    users[0].first_name, users[0].last_name = "Jamie", "Lee"
    users[0].save()
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    message, _ = send_message(actor=users[0], conversation=conversation, body="see you tomorrow")
    notify_message(message=message, recipients=[users[1]])

    assert len(sent) == 1
    assert sent[0]["title"] == "Jamie Lee"
    assert sent[0]["body"] == "see you tomorrow"
    assert sent[0]["title"] != sent[0]["body"]
    assert sent[0]["title"] != "messaging.new_message"
    assert sent[0]["body"] != "messaging.new_message"


@pytest.mark.django_db
def test_notify_message_unknown_sender_falls_back_to_the_unknown_sender_title(domain, monkeypatch):
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    message, _ = send_message(actor=users[0], conversation=conversation, body="hi")
    message.sender = None
    message.save(update_fields=["sender"])
    notify_message(message=message, recipients=[users[1]])

    assert sent[0]["title"] == "Unbekannter Absender"


@pytest.mark.django_db
def test_notify_message_body_is_truncated_at_the_conversation_excerpt_length(domain, monkeypatch):
    from django_core_micha.messaging.serializers import CONVERSATION_EXCERPT_LENGTH

    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    long_text = "x" * (CONVERSATION_EXCERPT_LENGTH + 50)
    message, _ = send_message(actor=users[0], conversation=conversation, body=long_text)
    notify_message(message=message, recipients=[users[1]])

    # The cut must be a deliberate, visible product decision (an ellipsis), not a bare
    # slice indistinguishable from a message that just happened to end there.
    assert sent[0]["body"].endswith("…")
    assert sent[0]["body"] == long_text[: CONVERSATION_EXCERPT_LENGTH - 1] + "…"
    assert len(sent[0]["body"]) == CONVERSATION_EXCERPT_LENGTH


@pytest.mark.django_db
def test_notify_message_body_at_or_under_the_limit_gets_no_ellipsis(domain, monkeypatch):
    from django_core_micha.messaging.serializers import CONVERSATION_EXCERPT_LENGTH

    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    exact_text = "y" * CONVERSATION_EXCERPT_LENGTH
    message, _ = send_message(actor=users[0], conversation=conversation, body=exact_text)
    notify_message(message=message, recipients=[users[1]])

    assert sent[0]["body"] == exact_text
    assert not sent[0]["body"].endswith("…")


@pytest.mark.django_db
def test_notify_message_attachment_only_send_gets_a_kind_fallback_body(domain, monkeypatch):
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    message, _ = send_message(actor=users[0], conversation=conversation, body="")
    MessageAttachment.objects.create(
        message=message, blob_key="key", filename="photo.png", content_type="image/png",
        byte_size=10, sha256="0" * 64,
    )
    notify_message(message=message, recipients=[users[1]])

    assert sent[0]["body"] == "hat einen Anhang gesendet"


@pytest.mark.django_db
def test_notify_message_soft_deleted_message_never_leaks_its_old_text(domain, monkeypatch):
    # Guards against a push composed after the message was deleted still carrying the
    # original body -- the WO's explicit non-vacuity condition for this test.
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    message, _ = send_message(actor=users[0], conversation=conversation, body="a secret I shouldn't send")
    message.deleted_at = timezone.now()
    message.save(update_fields=["deleted_at"])
    notify_message(message=message, recipients=[users[1]])

    assert "a secret I shouldn't send" not in sent[0]["body"]
    assert sent[0]["body"] == "Diese Nachricht wurde gelöscht"


@pytest.mark.django_db
def test_notify_message_does_not_leak_between_two_independent_sends(domain, monkeypatch):
    # A recipient's push must only ever carry the content of the message that
    # actually notified them -- never a sibling message's sender/text.
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    NotificationPreference.objects.create(user=users[1], push_opt_in=True)
    sent = _capture_push(monkeypatch)

    first, _ = send_message(actor=users[0], conversation=conversation, body="first message")
    notify_message(message=first, recipients=[users[1]])
    second, _ = send_message(actor=users[0], conversation=conversation, body="second message")
    notify_message(message=second, recipients=[users[1]])

    assert [call["body"] for call in sent] == ["first message", "second message"]
    assert "second message" not in sent[0]["body"]
    assert "first message" not in sent[1]["body"]


@pytest.mark.django_db
def test_notify_message_preview_off_hides_sender_and_text_but_keeps_the_push_actionable(domain, monkeypatch):
    app, conversation, users = domain
    register_messaging_notification_type(app.app_key)
    users[0].first_name, users[0].last_name = "Jamie", "Lee"
    users[0].save()
    NotificationPreference.objects.create(user=users[1], push_opt_in=True, push_preview_opt_in=False)
    sent = _capture_push(monkeypatch)

    message, _ = send_message(actor=users[0], conversation=conversation, body="sensitive body text")
    notify_message(message=message, recipients=[users[1]])

    assert sent[0]["title"] == "Jamie Lee"
    assert "sensitive body text" not in sent[0]["body"]
    assert "Jamie Lee" not in sent[0]["body"]
    assert sent[0]["body"] == "Neue Nachricht"


@pytest.mark.django_db
def test_push_preview_preference_defaults_to_on_for_a_user_with_no_preference_row(domain):
    _, _, users = domain
    assert _push_preview_enabled(users[1]) is True
