from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from django_core_micha.messaging.crypto import register_messaging_app
from django_core_micha.messaging.models import Conversation, ConversationParticipant, MessagingApp, MessagingScope
from django_core_micha.messaging.notifications import (
    messaging_notification_type_key,
    notify_message,
    register_messaging_notification_type,
)
from django_core_micha.messaging.policy import MembershipSnapshot, register_messaging_policy, unregister_messaging_policy
from django_core_micha.messaging.services import send_message
from django_core_micha.notifications.models import Notification
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
