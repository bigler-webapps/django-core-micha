from hashlib import sha256

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class PushSubscription(models.Model):
    """A browser-push subscription; one user can register several devices."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    ua = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class NotificationPreference(models.Model):
    """Per-user delivery-channel consent."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email_opt_in = models.BooleanField(default=False)
    push_opt_in = models.BooleanField(default=False)


class NotificationChannelDefault(models.Model):
    """Per-user channel default; ``set_channel_default`` upserts a default for callers."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel = models.CharField(max_length=16)
    enabled = models.BooleanField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "channel"],
                name="uniq_notification_channel_default",
            ),
        ]
        indexes = [models.Index(fields=["user", "channel"], name="notif_chdefault_user_channel")]

    @classmethod
    def set_channel_default(cls, user, channel, enabled):
        """Create or update ``user``'s default for ``channel``."""

        return cls.objects.update_or_create(
            user=user,
            channel=channel,
            defaults={"enabled": bool(enabled)},
        )[0]


class NotificationCategoryChannelPreference(models.Model):
    """Per-user category-channel override; ``set_category_channel`` upserts an override."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    category = models.CharField(max_length=32)
    channel = models.CharField(max_length=16)
    enabled = models.BooleanField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category", "channel"],
                name="uniq_notification_category_channel",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "category", "channel"],
                name="notif_catpref_user_cat_channel",
            ),
        ]

    @classmethod
    def set_category_channel(cls, user, category, channel, enabled):
        """Create or update ``user``'s category override for ``channel``."""

        return cls.objects.update_or_create(
            user=user,
            category=category,
            channel=channel,
            defaults={"enabled": bool(enabled)},
        )[0]


class AbstractNotification(models.Model):
    """Base for a consuming app's concrete notification/inbox model."""

    class Level(models.TextChoices):
        INFO = "info", "Info"
        UPDATE = "update", "Update"
        PROBLEM = "problem", "Problem"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    level = models.CharField(max_length=16, choices=Level.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    source = models.CharField(max_length=255, blank=True)
    url = models.CharField(max_length=2048, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


def get_notification_model():
    """Return the configured concrete inbox model, or ``None`` when disabled."""

    model_label = getattr(settings, "NOTIFICATION_MODEL", "")
    if not model_label:
        return None
    try:
        return apps.get_model(model_label)
    except (LookupError, ValueError):
        return None


class NotificationManager(models.Manager):
    """Manager methods for canonical notification creation."""

    def get_or_create_by_dedup(
        self,
        *,
        notification_type,
        category,
        notifiable=None,
        content,
        urgency="normal",
        expires_at=None,
    ):
        """Return the logical message identified by its deterministic deduplication key."""

        dedup_key = self.model.build_dedup_key(notification_type, notifiable)
        defaults = {
            "notification_type": notification_type,
            "category": category,
            "urgency": urgency,
            "content": content,
            "expires_at": expires_at,
        }
        if notifiable is not None:
            defaults["content_type"] = ContentType.objects.get_for_model(notifiable)
            defaults["object_id"] = str(notifiable.pk)
        return self.get_or_create(dedup_key=dedup_key, defaults=defaults)


class Notification(models.Model):
    """One logical message, deduplicated by a SHA-256 key of its type and target identity.

    One notification may have many recipient rows. ``dedup_key`` identifies the logical
    message, not an individual recipient: a message to N users is one Notification plus
    N NotificationRecipient rows. The key hashes ``notification_type`` and the notifiable
    content-type label and object ID (or an explicit no-target marker).
    """

    notification_type = models.CharField(max_length=64, db_index=True)
    category = models.CharField(max_length=32, db_index=True)
    urgency = models.CharField(max_length=16, default="normal")
    content = models.JSONField()
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    # CharField supports heterogeneous notifiable primary-key types across consumer apps.
    object_id = models.CharField(max_length=64, null=True, blank=True)
    notifiable = GenericForeignKey("content_type", "object_id")
    dedup_key = models.CharField(max_length=128)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = NotificationManager()

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["notification_type", "dedup_key"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["dedup_key"], name="uniq_notification_dedup_key"),
        ]

    @classmethod
    def build_dedup_key(cls, notification_type, notifiable):
        """Build the SHA-256 logical-message key from a type and optional target object."""

        if notifiable is None:
            target_identity = "none:none"
        else:
            content_type = ContentType.objects.get_for_model(notifiable)
            target_identity = f"{content_type.app_label}.{content_type.model}:{notifiable.pk}"
        return sha256(f"{notification_type}:{target_identity}".encode()).hexdigest()


class NotificationRecipient(models.Model):
    """Per-recipient notification status used by all notification projections."""

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_recipients",
    )
    seen_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("notification", "user")
        indexes = [models.Index(fields=["user", "done_at"])]


class NotificationDelivery(models.Model):
    """Per-recipient, per-channel delivery attempt state."""

    recipient = models.ForeignKey(
        NotificationRecipient,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    channel = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default="pending")
    retries = models.PositiveSmallIntegerField(default=0)
    digest_threshold = models.CharField(max_length=8, null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("recipient", "channel", "digest_threshold")
