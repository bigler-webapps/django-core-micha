import datetime
import json

import pytest
from channels_redis.serializers import registry
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.renderers import JSONRenderer

from django_core_micha.messaging.crypto import decrypt_text, register_messaging_app
from django_core_micha.messaging.models import (Conversation, ConversationParticipant, Message, MessagingApp,
                                                  MessagingAuditEvent, MessagingScope, PollVote)
from django_core_micha.messaging.policy import MembershipSnapshot, register_messaging_policy, unregister_messaging_policy
from django_core_micha.messaging.services import (
    MessagingPermissionDenied,
    add_reaction,
    archive_conversation,
    batch_read_status,
    break_glass_read,
    close_poll,
    create_conversation,
    create_poll,
    edit_message,
    mark_read,
    mark_thread_read,
    open_direct,
    read_status,
    reconcile_membership,
    remove_reaction,
    resolve_live_recipients,
    send_message,
    soft_delete_message,
    vote_poll,
)
from django_core_micha.messaging import services as services_module
from django_core_micha.messaging.serializers import serialize_message


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
def test_read_status_has_no_delivered_count_and_all_read_flips_when_all_recipients_read(domain):
    _, conversation, users, _ = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="status")

    initial = read_status(actor=users[0], message=message)
    assert initial["all_read"] is False and "delivered_count" not in initial
    mark_read(actor=users[1], conversation=conversation)
    mark_read(actor=users[2], conversation=conversation)
    assert read_status(actor=users[0], message=message)["all_read"] is True


@pytest.mark.django_db
def test_read_status_exposes_read_count_and_recipient_count_to_a_team_member(domain):
    _, conversation, users, policy = domain
    third = get_user_model().objects.create_user(username="third-reader")
    ConversationParticipant.objects.create(conversation=conversation, user=third)
    message, _ = send_message(actor=users[0], conversation=conversation, body="bus leaves at 14:00")
    mark_read(actor=users[1], conversation=conversation)

    policy.rights = frozenset({"read_receipt_detail"})
    status = read_status(actor=users[0], message=message)

    # Non-sender, non-removed participants: users[1], users[2] (muted, still counted), third = 3.
    assert status["recipient_count"] == 3
    assert status["read_count"] == 1
    assert status["all_read"] is False


@pytest.mark.django_db
def test_read_count_equals_recipient_count_and_all_read_agree_when_everyone_has_read(domain):
    _, conversation, users, policy = domain
    third = get_user_model().objects.create_user(username="third-reader-2")
    ConversationParticipant.objects.create(conversation=conversation, user=third)
    message, _ = send_message(actor=users[0], conversation=conversation, body="status")
    for reader in (users[1], users[2], third):
        mark_read(actor=reader, conversation=conversation)

    policy.rights = frozenset({"read_receipt_detail"})
    status = read_status(actor=users[0], message=message)

    assert status["read_count"] == status["recipient_count"] == 3
    assert status["all_read"] is True


@pytest.mark.django_db
def test_read_status_all_read_is_false_not_vacuously_true_with_zero_recipients(domain):
    """MSG-10 scope B: `all_read = not participants.exclude(...).exists()` is vacuously True
    when `participants` is empty -- a message nobody can receive must not report "read by
    everyone". The fixture's shared conversation always has other participants, so this
    needs a genuinely empty one (sender only), not a populated one filtered down."""
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    lonely_conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
    ConversationParticipant.objects.create(conversation=lonely_conversation, user=users[0])
    message = Message.objects.create(conversation=lonely_conversation, sender=users[0], body="alone")

    policy.rights = frozenset({"read_receipt_detail"})
    status = read_status(actor=users[0], message=message)

    assert status["all_read"] is False
    assert status["recipient_count"] == 0


@pytest.mark.django_db
def test_batch_read_status_all_read_is_false_not_vacuously_true_with_zero_recipients(domain):
    """Same defect, same fix, in the batch aggregate's own independent computation
    (`all(...) if non_sender_rows else True` is the identical vacuous-truth pattern)."""
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    lonely_conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
    ConversationParticipant.objects.create(conversation=lonely_conversation, user=users[0])
    message = Message.objects.create(conversation=lonely_conversation, sender=users[0], body="alone")

    policy.rights = frozenset({"read_receipt_detail"})
    result = batch_read_status(actor=users[0], message_ids=[str(message.id)])

    assert result[str(message.id)]["all_read"] is False
    assert result[str(message.id)]["recipient_count"] == 0


@pytest.mark.django_db
def test_read_count_and_recipient_detail_share_one_gate_absent_for_ordinary_sender(domain):
    _, conversation, users, policy = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="status")

    policy.rights = frozenset()
    ordinary_view = read_status(actor=users[0], message=message)
    assert "read_count" not in ordinary_view
    assert "recipient_count" not in ordinary_view
    assert "recipient_detail" not in ordinary_view

    policy.rights = frozenset({"read_receipt_detail"})
    team_view = read_status(actor=users[0], message=message)
    assert "read_count" in team_view
    assert "recipient_count" in team_view
    assert "recipient_detail" in team_view


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
@pytest.mark.parametrize(
    ("kind", "right"),
    [
        (Conversation.Kind.MANAGED, "create_managed"),
        (Conversation.Kind.GROUP, "open_group"),
        (Conversation.Kind.BROADCAST, "open_broadcast"),
    ],
)
def test_keyed_conversation_creation_is_idempotent_and_reconciles_provider_membership(domain, kind, right):
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    policy.rights = frozenset({right})
    policy.snapshot = MembershipSnapshot([users[1]], remove_absent=True)

    first = create_conversation(
        actor=users[0], app=app, scope=scope, kind=kind, external_key=f"{kind}:stable",
    )
    policy.snapshot = MembershipSnapshot([users[2]], remove_absent=True)
    second = create_conversation(
        actor=users[0], app=app, scope=scope, kind=kind, external_key=f"{kind}:stable", participant_users=[users[3]],
    )

    assert second.pk == first.pk
    assert Conversation.objects.filter(app=app, scope=scope, kind=kind, external_key=f"{kind}:stable").count() == 1
    assert ConversationParticipant.objects.get(conversation=first, user=users[1]).removed_at is not None
    assert ConversationParticipant.objects.get(conversation=first, user=users[2]).removed_at is None
    assert ConversationParticipant.objects.get(conversation=first, user=users[3]).membership_source == "manual"


@pytest.mark.django_db
def test_authorized_actor_joins_keyed_conversation_on_reopen(domain):
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    policy.rights = frozenset({"open_group"})
    policy.snapshot = MembershipSnapshot([])
    first = create_conversation(actor=users[0], app=app, scope=scope, kind=Conversation.Kind.GROUP, external_key="group:stable")

    second = create_conversation(actor=users[1], app=app, scope=scope, kind=Conversation.Kind.GROUP, external_key="group:stable")

    assert second.pk == first.pk
    assert ConversationParticipant.objects.filter(conversation=first, user=users[1], removed_at__isnull=True).exists()


@pytest.mark.django_db
def test_keyed_conversation_reopen_still_requires_moderation_right(domain):
    app, _, users, policy = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    policy.rights = frozenset({"open_group"})
    create_conversation(actor=users[0], app=app, scope=scope, kind=Conversation.Kind.GROUP, external_key="group:stable")

    policy.rights = frozenset()
    with pytest.raises(MessagingPermissionDenied):
        create_conversation(actor=users[1], app=app, scope=scope, kind=Conversation.Kind.GROUP, external_key="group:stable")


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


def _capture_frames(monkeypatch):
    # transaction.on_commit callbacks never fire inside pytest-django's default
    # per-test atomic wrapper — django_capture_on_commit_callbacks(execute=True)
    # (used by every test below) is what actually runs them.
    sent = []
    monkeypatch.setattr("django_core_micha.messaging.realtime.push_to_users", lambda users, frame: sent.append((list(users), frame)))
    return sent


def _capture_delivered_events(monkeypatch):
    """Capture events after the shared delivery chokepoint without Redis."""
    delivered = []

    class Layer:
        async def group_send(self, group, event):
            delivered.append((group, event))

    layer = Layer()
    monkeypatch.setattr("channels.layers.get_channel_layer", lambda: layer)
    monkeypatch.setattr(
        "asgiref.sync.async_to_sync",
        lambda fn: lambda *args, **kwargs: delivered.append((args[0], args[1])),
    )
    return delivered


def _messaging_frames(delivered, frame_type):
    return [event["payload"] for _, event in delivered if event["payload"]["type"] == frame_type]


@pytest.mark.django_db
def test_message_and_conversation_upsert_frames_survive_production_msgpack_encoding(
    domain, monkeypatch, django_capture_on_commit_callbacks,
):
    _, conversation, users, _ = domain
    delivered = _capture_delivered_events(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        send_message(actor=users[0], conversation=conversation, body="serializable")

    serializer = registry.get_serializer("msgpack")
    for frame_type in ("message", "conversation_upsert"):
        frames = _messaging_frames(delivered, frame_type)
        assert frames
        serializer.serialize(frames[0])


@pytest.mark.django_db
def test_message_edited_frame_survives_production_msgpack_encoding(
    domain, monkeypatch, django_capture_on_commit_callbacks,
):
    _, conversation, users, _ = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="original")
    delivered = _capture_delivered_events(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        edit_message(actor=users[0], message=message, body="edited")

    frame = _messaging_frames(delivered, "message_edited")[0]
    registry.get_serializer("msgpack").serialize(frame)


@pytest.mark.django_db
def test_message_websocket_payload_matches_rest_json_shape(
    domain, monkeypatch, django_capture_on_commit_callbacks,
):
    _, conversation, users, _ = domain
    delivered = _capture_delivered_events(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        message, _ = send_message(actor=users[0], conversation=conversation, body="same shape")

    websocket_message = _messaging_frames(delivered, "message")[0]["message"]
    rest_message = serialize_message(message)
    registry.get_serializer("msgpack").serialize(_messaging_frames(delivered, "message")[0])
    assert websocket_message == json.loads(JSONRenderer().render(rest_message))


@pytest.mark.django_db
def test_delivery_failure_is_error_logged_without_rolling_back_message(
    domain, monkeypatch, django_capture_on_commit_callbacks, caplog,
):
    _, conversation, users, _ = domain

    class Layer:
        async def group_send(self, group, event):
            raise RuntimeError("channel unavailable")

    monkeypatch.setattr("channels.layers.get_channel_layer", lambda: Layer())
    monkeypatch.setattr(
        "asgiref.sync.async_to_sync",
        lambda fn: lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("channel unavailable")),
    )

    with django_capture_on_commit_callbacks(execute=True):
        message, created = send_message(actor=users[0], conversation=conversation, body="durable")

    assert created
    assert Message.objects.filter(pk=message.pk).exists()
    assert "ERROR" in caplog.text
    assert "frame_type='message'" in caplog.text


@pytest.mark.django_db
def test_reaction_add_and_remove_publish_aggregate_frame_to_live_recipients(domain, monkeypatch, django_capture_on_commit_callbacks):
    _, conversation, users, _ = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="react to me")
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        add_reaction(actor=users[1], message=message, emoji="\U0001F44D")
    with django_capture_on_commit_callbacks(execute=True):
        remove_reaction(actor=users[1], message=message, emoji="\U0001F44D")

    reaction_frames = [(recipients, frame) for recipients, frame in sent if frame["type"] == "reaction"]
    assert len(reaction_frames) == 2
    recipients, added = reaction_frames[0]
    assert {u.pk for u in recipients} == {users[0].pk, users[1].pk}  # sender+live; muted/removed excluded
    assert added["reactions"] == [{"emoji": "\U0001F44D", "count": 1}]
    assert added["message_id"] == str(message.id)
    # Aggregate only — no per-user emoji ownership on the wire.
    assert set(added).isdisjoint({"user_id", "users"})
    _, removed = reaction_frames[1]
    assert removed["reactions"] == []


@pytest.mark.django_db
def test_poll_vote_and_close_publish_poll_updated_without_voted_option_ids(domain, monkeypatch, django_capture_on_commit_callbacks):
    _, conversation, users, _ = domain
    poll, created = create_poll(actor=users[0], conversation=conversation, question="Coffee or tea?", options=["Coffee", "Tea"])
    assert created
    option = poll.options.first()
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        vote_poll(actor=users[1], poll=poll, option_ids=[option.id])
    with django_capture_on_commit_callbacks(execute=True):
        close_poll(actor=users[0], poll=poll)

    poll_frames = [frame for _, frame in sent if frame["type"] == "poll_updated"]
    assert len(poll_frames) == 2  # vote, then close
    for frame in poll_frames:
        assert "voted_option_ids" not in frame["poll"]
        assert frame["poll"]["question"] == "Coffee or tea?"
        assert frame["envelope"] == "messaging" and frame["event_id"]
    # The load-bearing invariant: identical payload regardless of who ends up receiving it.
    vote_frame, close_frame = poll_frames
    assert vote_frame["poll"]["options"][0]["voters"] == [users[1].pk]
    assert close_frame["poll"]["closed_at"] is not None


@pytest.mark.django_db
def test_vote_poll_switching_to_a_different_option_still_clears_the_prior_one_singlechoice(domain):
    """Regression guard: the multi-select retraction fix above must not disturb
    single-choice's existing switch-vote behaviour."""
    _, conversation, users, _ = domain
    poll, _ = create_poll(actor=users[0], conversation=conversation, question="q", options=["A", "B"])
    option_a, option_b = poll.options.order_by("order")

    vote_poll(actor=users[1], poll=poll, option_ids=[option_a.id])
    vote_poll(actor=users[1], poll=poll, option_ids=[option_b.id])

    assert list(PollVote.objects.filter(option__poll=poll, user=users[1]).values_list("option_id", flat=True)) == [option_b.id]


@pytest.mark.django_db
def test_vote_poll_resending_a_smaller_option_set_retracts_the_omitted_vote_multiselect(domain):
    """`option_ids` is the caller's complete authoritative vote set for both single- and
    multi-select polls -- previously only the single-choice branch cleared prior votes
    first; multi-select only ever `bulk_create`d, so voting [A,B] then re-voting [A] never
    actually retracted B (untoggling an option was silently a no-op)."""
    _, conversation, users, _ = domain
    poll, _ = create_poll(actor=users[0], conversation=conversation, question="q", options=["A", "B", "C"], allow_multiple=True)
    option_a, option_b, _ = poll.options.order_by("order")

    vote_poll(actor=users[1], poll=poll, option_ids=[option_a.id, option_b.id])
    assert set(PollVote.objects.filter(option__poll=poll, user=users[1]).values_list("option_id", flat=True)) == {option_a.id, option_b.id}

    vote_poll(actor=users[1], poll=poll, option_ids=[option_a.id])
    assert set(PollVote.objects.filter(option__poll=poll, user=users[1]).values_list("option_id", flat=True)) == {option_a.id}


@pytest.mark.django_db
def test_vote_poll_resending_an_empty_option_set_retracts_all_votes_multiselect(domain):
    _, conversation, users, _ = domain
    poll, _ = create_poll(actor=users[0], conversation=conversation, question="q", options=["A", "B"], allow_multiple=True)
    option_a, option_b = poll.options.order_by("order")

    vote_poll(actor=users[1], poll=poll, option_ids=[option_a.id, option_b.id])
    vote_poll(actor=users[1], poll=poll, option_ids=[])

    assert not PollVote.objects.filter(option__poll=poll, user=users[1]).exists()


@pytest.mark.django_db
def test_vote_poll_revoting_an_already_selected_option_does_not_duplicate(domain):
    """Guards the `ignore_conflicts=True` re-create path against the unique (option, user)
    constraint -- re-sending an option already voted for must stay a no-op, not error."""
    _, conversation, users, _ = domain
    poll, _ = create_poll(actor=users[0], conversation=conversation, question="q", options=["A", "B"], allow_multiple=True)
    option_a, option_b = poll.options.order_by("order")

    vote_poll(actor=users[1], poll=poll, option_ids=[option_a.id])
    vote_poll(actor=users[1], poll=poll, option_ids=[option_a.id, option_b.id])

    assert PollVote.objects.filter(option__poll=poll, user=users[1], option=option_a).count() == 1


@pytest.mark.django_db
def test_message_and_message_edited_frames_never_gain_voted_option_ids_when_poll(domain, monkeypatch, django_capture_on_commit_callbacks):
    """The viewer-independence rule applies retroactively to the already-shipped
    message/message_edited frames once they embed a poll (design amendment)."""
    _, conversation, users, _ = domain
    poll, _ = create_poll(actor=users[0], conversation=conversation, question="q", options=["a", "b"])
    PollVote.objects.create(option=poll.options.first(), user=users[0])
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        edit_message(actor=users[0], message=poll.message, body=None)

    edited = [frame for _, frame in sent if frame["type"] == "message_edited"]
    assert len(edited) == 1
    assert "voted_option_ids" not in edited[0]["message"]["poll"]


@pytest.mark.django_db
def test_message_and_message_edited_frames_never_gain_thread_last_read_at(domain, monkeypatch, django_capture_on_commit_callbacks):
    """MSG-2d's second viewer-specific field must be held to the same rule as
    voted_option_ids: thread_last_read_at is REST-only (added by views.py, never by
    serialize_message itself), so it must never appear on a fanned-out frame — even
    after the actor has an actual MessageThreadReceipt for this root."""
    _, conversation, users, _ = domain
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        root, _ = send_message(actor=users[0], conversation=conversation, body="root")
    with django_capture_on_commit_callbacks(execute=True):
        reply, _ = send_message(actor=users[1], conversation=conversation, body="a reply", reply_to=root)
    mark_thread_read(actor=users[0], root=root)  # gives users[0] an actual receipt for `root`
    sent.clear()

    # A fresh "message" frame, sent after the receipt above exists.
    with django_capture_on_commit_callbacks(execute=True):
        send_message(actor=users[1], conversation=conversation, body="another reply", reply_to=root)
    # And a "message_edited" frame on the root itself, the message the receipt is actually for.
    with django_capture_on_commit_callbacks(execute=True):
        edit_message(actor=users[0], message=root, body="root, edited")

    message = [frame for _, frame in sent if frame["type"] == "message"]
    edited = [frame for _, frame in sent if frame["type"] == "message_edited"]
    assert len(message) == 1 and len(edited) == 1
    assert "thread_last_read_at" not in message[0]["message"]
    assert "thread_last_read_at" not in edited[0]["message"]
    assert edited[0]["message"]["reply_count"] == 2
    assert edited[0]["message"]["last_reply_at"] > reply.created_at


@pytest.mark.django_db
def test_watermark_frames_fire_only_on_actual_advance(domain, monkeypatch, django_capture_on_commit_callbacks):
    _, conversation, users, _ = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="hi")
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        mark_read(actor=users[1], conversation=conversation)
    with django_capture_on_commit_callbacks(execute=True):
        mark_thread_read(actor=users[1], root=message)
    assert {frame["type"] for _, frame in sent} == {"read_state", "thread_read_state"}
    assert len(sent) == 2

    sent.clear()
    # Re-marking with an earlier or equal timestamp is a no-op — must not re-fire.
    earlier = timezone.now() - datetime.timedelta(hours=1)
    with django_capture_on_commit_callbacks(execute=True):
        mark_read(actor=users[1], conversation=conversation, read_at=earlier)
    with django_capture_on_commit_callbacks(execute=True):
        mark_thread_read(actor=users[1], root=message, read_at=earlier)
    assert sent == []

    later = timezone.now() + datetime.timedelta(hours=1)
    with django_capture_on_commit_callbacks(execute=True):
        mark_read(actor=users[1], conversation=conversation, read_at=later)
    assert len([f for _, f in sent if f["type"] == "read_state"]) == 1


@pytest.mark.django_db
def test_archive_conversation_frame_is_participant_local(domain, monkeypatch, django_capture_on_commit_callbacks):
    _, conversation, users, _ = domain
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        archive_conversation(actor=users[1], conversation=conversation, archived=True)

    frames = [(recipients, frame) for recipients, frame in sent if frame["type"] == "conversation_archived"]
    assert len(frames) == 1
    recipients, frame = frames[0]
    assert [u.pk for u in recipients] == [users[1].pk]  # only the archiving participant, not the conversation
    assert frame["archived"] is True


@pytest.mark.django_db
def test_reconcile_membership_publishes_participant_changed_once(domain, monkeypatch, django_capture_on_commit_callbacks):
    _, conversation, users, policy = domain
    policy.snapshot = MembershipSnapshot([users[1]], external_key="all", remove_absent=False)
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        reconcile_membership(conversation=conversation)

    frames = [frame for _, frame in sent if frame["type"] == "participant_changed"]
    assert len(frames) == 1


@pytest.mark.django_db
def test_send_message_and_open_direct_publish_conversation_upsert_without_participant_fields(domain, monkeypatch, django_capture_on_commit_callbacks):
    app, conversation, users, _ = domain
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        send_message(actor=users[0], conversation=conversation, body="hi")

    upserts = [frame for _, frame in sent if frame["type"] == "conversation_upsert"]
    assert len(upserts) == 1
    # Viewer-independent projection only — no participant-scoped archived_at/muted/etc,
    # which would otherwise leak one recipient's state to every other fanned-out recipient.
    assert set(upserts[0]).isdisjoint({"archived_at", "muted", "email_enabled", "push_enabled"})
    assert upserts[0]["last_message"]["excerpt"] == "hi"

    sent.clear()
    scope = MessagingScope.objects.get(app=app, kind="global")
    with django_capture_on_commit_callbacks(execute=True):
        open_direct(actor=users[0], target=users[1], app=app, scope=scope)
    direct_upserts = [frame for _, frame in sent if frame["type"] == "conversation_upsert"]
    assert len(direct_upserts) == 1
    assert direct_upserts[0]["kind"] == "direct"


@pytest.mark.django_db
def test_reopening_an_existing_direct_conversation_does_not_republish_upsert(domain, monkeypatch, django_capture_on_commit_callbacks):
    """open_direct's get_or_create is a routine no-op on every re-open of an existing DM
    (e.g. a user reopening a chat thread) — that must not re-fire conversation_upsert on
    every call, which would be an unbounded fan-out storm for a frequently-reopened DM."""
    app, _, users, _ = domain
    scope = MessagingScope.objects.get(app=app, kind="global")
    sent = _capture_frames(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True):
        open_direct(actor=users[0], target=users[1], app=app, scope=scope)
    assert len([f for _, f in sent if f["type"] == "conversation_upsert"]) == 1
    sent.clear()

    with django_capture_on_commit_callbacks(execute=True):
        open_direct(actor=users[0], target=users[1], app=app, scope=scope)

    assert [f for _, f in sent if f["type"] == "conversation_upsert"] == []


@pytest.mark.django_db
def test_reaction_frames_do_not_republish_on_a_no_op(domain, monkeypatch, django_capture_on_commit_callbacks):
    _, conversation, users, _ = domain
    message, _ = send_message(actor=users[0], conversation=conversation, body="react to me")
    with django_capture_on_commit_callbacks(execute=True):
        add_reaction(actor=users[1], message=message, emoji="\U0001F44D")
    sent = _capture_frames(monkeypatch)

    with django_capture_on_commit_callbacks(execute=True):
        add_reaction(actor=users[1], message=message, emoji="\U0001F44D")  # already reacted — no-op
    with django_capture_on_commit_callbacks(execute=True):
        remove_reaction(actor=users[1], message=message, emoji="\U0001F604")  # never reacted — no-op

    assert [f for _, f in sent if f["type"] == "reaction"] == []
