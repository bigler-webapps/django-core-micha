import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q

from .fields import EncryptedTextField


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class MessagingApp(UUIDModel):
    app_key = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True)
    keyset_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)


class MessagingTenantResolutionError(ValueError):
    """Raised when a request has no unambiguous server-side messaging tenant."""


def resolve_messaging_app(*, scope=None):
    """Resolve the request tenant without consulting client input or a policy.

    A scope is authoritative.  Global DMs are only safe in the single-active-app
    deployment topology; a future User-to-MessagingApp binding belongs here.
    """
    if scope is not None:
        return scope.app
    apps = list(MessagingApp.objects.filter(active=True)[:2])
    if len(apps) != 1:
        raise MessagingTenantResolutionError(
            "Global-scope conversation requires an explicit scope in a multi-app deployment."
        )
    return apps[0]


class MessagingScope(UUIDModel):
    class Kind(models.TextChoices):
        CONTAINER = "container", "Container"
        OBJECT = "object", "Object"
        GLOBAL = "global", "Global"

    app = models.ForeignKey(MessagingApp, on_delete=models.CASCADE, related_name="scopes")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64, null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["app", "kind", "content_type", "object_id"], name="msg_scope_app_kind_object_uniq"),
            models.CheckConstraint(
                condition=(Q(kind="global", content_type__isnull=True, object_id__isnull=True) | Q(kind__in=["container", "object"], content_type__isnull=False, object_id__isnull=False)),
                name="msg_scope_ref_matches_kind",
            ),
        ]


class Conversation(UUIDModel):
    class Kind(models.TextChoices):
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"
        BROADCAST = "broadcast", "Broadcast"
        MANAGED = "managed", "Managed"
        OBJECT_THREAD = "object_thread", "Object thread"

    class RetentionPolicy(models.TextChoices):
        NEVER = "never", "Never"

    app = models.ForeignKey(MessagingApp, on_delete=models.CASCADE, related_name="conversations")
    scope = models.ForeignKey(MessagingScope, on_delete=models.CASCADE, related_name="conversations")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    title = EncryptedTextField(null=True, blank=True, app_key_accessor="messaging_app_key")
    external_key = models.CharField(max_length=128, null=True, blank=True)
    user_low = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="messaging_conversations_low")
    user_high = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="messaging_conversations_high")
    last_message_at = models.DateTimeField(null=True, blank=True)
    retention_policy = models.CharField(max_length=16, choices=RetentionPolicy.choices, default=RetentionPolicy.NEVER)
    delete_after = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def messaging_app_key(self):
        return self.app.app_key

    class Meta:
        ordering = [models.F("last_message_at").desc(nulls_last=True), "-created_at"]
        constraints = [
            models.UniqueConstraint(condition=Q(kind="direct"), fields=["app", "scope", "user_low", "user_high"], name="msg_direct_app_scope_pair_uniq"),
            models.UniqueConstraint(condition=Q(kind__in=["managed", "broadcast"]), fields=["app", "scope", "kind", "external_key"], name="msg_managed_bcast_key_uniq"),
            models.CheckConstraint(condition=Q(kind="direct", user_low__isnull=False, user_high__isnull=False) | ~Q(kind="direct"), name="msg_direct_has_canonical_pair"),
        ]


class ConversationParticipant(UUIDModel):
    class MembershipSource(models.TextChoices):
        MANUAL = "manual", "Manual"
        PROVIDER = "provider", "Provider"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="messaging_participations")
    membership_source = models.CharField(max_length=16, choices=MembershipSource.choices, default=MembershipSource.MANUAL)
    last_delivered_at = models.DateTimeField(null=True, blank=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    muted = models.BooleanField(default=False)
    email_enabled = models.BooleanField(null=True, blank=True)
    push_enabled = models.BooleanField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "user"], name="msg_participant_pair_uniq")]


class Message(UUIDModel):
    class Kind(models.TextChoices):
        CHAT = "chat", "Chat"
        ANNOUNCEMENT = "announcement", "Announcement"
        POLL = "poll", "Poll"
        SYSTEM = "system", "System"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="messaging_sent_messages")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CHAT)
    title = EncryptedTextField(null=True, blank=True, app_key_accessor="messaging_app_key")
    body = EncryptedTextField(null=True, blank=True, app_key_accessor="messaging_app_key")
    link_target = EncryptedTextField(null=True, blank=True, app_key_accessor="messaging_app_key")
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies")
    client_request_id = models.UUIDField(null=True, blank=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="messaging_deleted_messages")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def messaging_app_key(self):
        return self.conversation.app.app_key

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["conversation", "sender", "client_request_id"], name="msg_client_request_uniq")]


class MessageReaction(UUIDModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="messaging_reactions")
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user", "emoji"], name="msg_reaction_uniq")]


class MessageAttachment(UUIDModel):
    class ScanState(models.TextChoices):
        UNSCANNED = "unscanned", "Unscanned"
        CLEAN = "clean", "Clean"
        REJECTED = "rejected", "Rejected"
        ERROR = "error", "Error"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    blob_key = EncryptedTextField(app_key_accessor="messaging_app_key")
    filename = EncryptedTextField(app_key_accessor="messaging_app_key")
    content_type = models.CharField(max_length=128)
    byte_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    order = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_key = EncryptedTextField(null=True, blank=True, app_key_accessor="messaging_app_key")
    scan_state = models.CharField(max_length=16, choices=ScanState.choices, default=ScanState.UNSCANNED)
    scan_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def messaging_app_key(self):
        return self.message.conversation.app.app_key

    class Meta:
        ordering = ["order", "id"]


class MessageThreadReceipt(UUIDModel):
    root = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="thread_receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="messaging_thread_receipts")
    last_read_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["root", "user"], name="msg_thread_receipt_uniq")]


class Poll(UUIDModel):
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="poll")
    question = EncryptedTextField(app_key_accessor="messaging_app_key")
    allow_multiple = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="messaging_created_polls")
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    @property
    def messaging_app_key(self):
        return self.message.conversation.app.app_key


class PollOption(UUIDModel):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="options")
    text = EncryptedTextField(app_key_accessor="messaging_app_key")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def messaging_app_key(self):
        return self.poll.message.conversation.app.app_key


class PollVote(UUIDModel):
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="messaging_poll_votes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["option", "user"], name="msg_poll_vote_uniq")]


class MessagingAuditEvent(UUIDModel):
    app = models.ForeignKey(MessagingApp, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="messaging_audit_events")
    action = models.CharField(max_length=64)
    target_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    target_object_id = models.CharField(max_length=64, null=True, blank=True)
    target = GenericForeignKey("target_content_type", "target_object_id")
    reason = models.TextField(blank=True)
    request_metadata = models.JSONField(default=dict, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["app", "created_at"], name="msg_audit_app_created_idx")]
