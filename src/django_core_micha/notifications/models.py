from django.apps import apps
from django.conf import settings
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
