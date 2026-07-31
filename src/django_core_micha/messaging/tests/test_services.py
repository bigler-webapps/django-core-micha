import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.messaging.crypto import decrypt_text, register_messaging_app
from django_core_micha.messaging.models import Conversation, ConversationParticipant, Message, MessagingApp, MessagingAuditEvent, MessagingScope
from django_core_micha.messaging.policy import MembershipSnapshot, register_messaging_policy, unregister_messaging_policy
from django_core_micha.messaging.services import (
    MessagingPermissionDenied,
    break_glass_read,
    create_conversation,
    edit_message,
    read_status,
    reconcile_membership,
    resolve_live_recipients,
    send_message,
    soft_delete_message,
)
from django_core_micha.messaging import services as services_module


class TestPolicy:
    rights = frozenset()
    recipients = []
    snapshot = None
    def can_open_direct(self, **kwargs): return True
    def can_view_conversation(self, **kwargs): return True
    def can_post(self, **kwargs): return True
    def moderation_rights(self, **kwargs): return self.rights
    def resolve_recipients(self, **kwargs): return self.recipients
    def provision_membership(self, **kwargs): return self.snapshot or MembershipSnapshot([])
    def validate_scope(self, **kwargs): return {}


@pytest.fixture
def domain(db):
    key = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"service-app": [key]}):
        register_messaging_app("service-app")
        app = MessagingApp.objects.create(app_key="service-app", keyset_id="test")
        scope = MessagingScope.objects.create(app=app, kind="global")
        conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
        users = [get_user_model().objects.create_user(username=name) for name in ("sender", "live", "muted", "removed")]
        for user in users: ConversationParticipant.objects.create(conversation=conversation, user=user)
        ConversationParticipant.objects.filter(conversation=conversation, user=users[2]).update(muted=True)
        ConversationParticipant.objects.filter(conversation=conversation, user=users[3]).update(removed_at="2026-01-01T00:00:00Z")
        policy = TestPolicy(); policy.recipients = users
        register_messaging_policy("service-app", policy)
        yield app, conversation, users, policy
    unregister_messaging_policy("service-app")


@pytest.mark.django_db
def test_send_idempotency_resolves_live_recipients_and_clears_archive(domain, monkeypatch):
    _, conversation, users, _ = domain
    ConversationParticipant.objects.filter(conversation=conversation, user=users[1]).update(archived_at="2026-01-01T00:00:00Z")
    published = []
    monkeypatch.setattr("django_core_micha.messaging.realtime.publish_messaging_event", lambda **kwargs: published.append(kwargs))
    message, created = send_message(actor=users[0], conversation=conversation, body="secret", client_request_id="12345678-1234-1234-1234-123456789abc")
    duplicate, duplicate_created = send_message(actor=users[0], conversation=conversation, body="changed", client_request_id="12345678-1234-1234-1234-123456789abc")
    assert created and not duplicate_created and duplicate.pk == message.pk
    assert [u.pk for u in resolve_live_recipients(conversation=conversation, sender=users[0])] == [users[1].pk]
    assert ConversationParticipant.objects.get(conversation=conversation, user=users[1]).archived_at is None


@pytest.mark.django_db
def test_provider_reconcile_upserts_and_only_removes_when_requested(domain):
    _, conversation, users, policy = domain
    policy.snapshot = MembershipSnapshot([users[1]], external_key="all", remove_absent=True)
    reconcile_membership(conversation=conversation)
    assert ConversationParticipant.objects.get(conversation=conversation, user=users[1]).removed_at is None
    # Manual rows are intentionally retained; only provider rows are removed.
    assert ConversationParticipant.objects.filter(conversation=conversation, membership_source="provider", removed_at__isnull=False).count() == 0
    assert conversation.external_key == "all"


@pytest.mark.django_db
def test_break_glass_writes_audit_for_denial_and_success(domain):
    _, conversation, users, policy = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="sensitive")
    with pytest.raises(MessagingPermissionDenied):
        break_glass_read(actor=users[1], message=message, reason="incident")
    assert MessagingAuditEvent.objects.filter(action="break_glass_read_denied").count() == 1
    policy.rights = frozenset({"read_receipt_detail"})
    assert break_glass_read(actor=users[1], message=message, reason="incident")["body"] == "sensitive"
    assert MessagingAuditEvent.objects.filter(action="break_glass_read").count() == 1


@pytest.mark.django_db
def test_direct_read_status_never_exposes_recipient_detail(domain):
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    direct = Conversation.objects.create(app=app, scope=scope, kind="direct", user_low=users[0], user_high=users[1])
    for user in users[:2]: ConversationParticipant.objects.create(conversation=direct, user=user)
    message = Message.objects.create(conversation=direct, sender=users[0], body="private")
    policy.rights = frozenset({"read_receipt_detail"})
    assert "recipient_detail" not in read_status(actor=users[1], message=message)


@pytest.mark.django_db
def test_direct_break_glass_is_always_denied_even_with_read_receipt_detail(domain):
    """read_receipt_detail grants read-STATUS visibility, never DM content decryption."""
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    direct = Conversation.objects.create(app=app, scope=scope, kind="direct", user_low=users[0], user_high=users[1])
    for user in users[:2]: ConversationParticipant.objects.create(conversation=direct, user=user)
    message = Message.objects.create(conversation=direct, sender=users[0], body="private")
    policy.rights = frozenset({"read_receipt_detail"})
    with pytest.raises(MessagingPermissionDenied):
        break_glass_read(actor=users[1], message=message, reason="incident")
    assert MessagingAuditEvent.objects.filter(action="break_glass_read_denied").count() == 1
    assert MessagingAuditEvent.objects.filter(action="break_glass_read").count() == 0


@pytest.mark.django_db
def test_edit_and_delete_require_author_or_edit_delete_any(domain):
    _, conversation, users, policy = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="original")

    with pytest.raises(MessagingPermissionDenied):
        edit_message(actor=users[1], message=message, body="hijacked")
    with pytest.raises(MessagingPermissionDenied):
        soft_delete_message(actor=users[1], message=message)

    app, _, _, _ = domain
    edit_message(actor=users[0], message=message, body="author edit")
    stored = Message.objects.get(pk=message.pk).body
    assert decrypt_text(app_key=app.app_key, value=stored) == "author edit"

    policy.rights = frozenset({"edit_any"})
    edit_message(actor=users[1], message=message, body="moderator edit")
    stored = Message.objects.get(pk=message.pk).body
    assert decrypt_text(app_key=app.app_key, value=stored) == "moderator edit"

    policy.rights = frozenset()
    with pytest.raises(MessagingPermissionDenied):
        soft_delete_message(actor=users[1], message=message)
    policy.rights = frozenset({"delete_any"})
    soft_delete_message(actor=users[1], message=message)
    assert Message.objects.get(pk=message.pk).deleted_at is not None


@pytest.mark.django_db
def test_create_conversation_requires_the_matching_moderation_right(domain):
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")

    policy.rights = frozenset()
    with pytest.raises(MessagingPermissionDenied):
        create_conversation(actor=users[0], app=app, scope=scope, kind=Conversation.Kind.GROUP)
    with pytest.raises(MessagingPermissionDenied):
        create_conversation(actor=users[0], app=app, scope=scope, kind=Conversation.Kind.BROADCAST)

    policy.rights = frozenset({"open_group"})
    group = create_conversation(actor=users[0], app=app, scope=scope, kind=Conversation.Kind.GROUP)
    assert group.kind == Conversation.Kind.GROUP

    policy.rights = frozenset({"open_broadcast"})
    broadcast = create_conversation(actor=users[0], app=app, scope=scope, kind=Conversation.Kind.BROADCAST)
    assert broadcast.kind == Conversation.Kind.BROADCAST


@pytest.mark.django_db
def test_send_message_recovers_from_concurrent_integrity_error_without_aborting_transaction(domain, monkeypatch):
    """Simulates two racing sends for the same client_request_id past the initial
    existence check (both miss it before either commits), forcing send_message
    into the create()/IntegrityError/recover path — the nested atomic() savepoint
    (R2 fix) must let the subsequent .get() run, not raise TransactionManagementError.
    Runs on SQLite here (repo test default), which does not abort the whole
    transaction on IntegrityError the way Postgres (the production engine) does,
    so this proves the recovery *logic* is correct but is not a substitute for a
    live-Postgres check of the transaction-abort behaviour itself.
    """
    _, conversation, users, _ = domain
    request_id = "22222222-2222-2222-2222-222222222222"
    winner = Message.objects.create(conversation=conversation, sender=users[0], body="first", client_request_id=request_id)

    real_filter = services_module.Message.objects.filter
    calls = {"n": 0}

    def flaky_filter(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return services_module.Message.objects.none()
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(services_module.Message.objects, "filter", flaky_filter)

    message, created = send_message(actor=users[0], conversation=conversation, body="second", client_request_id=request_id)

    assert not created
    assert message.pk == winner.pk
    # The outer transaction must still be usable after the recovered IntegrityError.
    assert ConversationParticipant.objects.filter(conversation=conversation).count() == 4
