from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

# Fixed platform-wide storage width (operator decision, ACT-1) — not configurable
# per app. A coarser store cannot be decomposed into hours after the fact, and the
# finest view wanted (a single day on an hourly grid) requires this floor.
BUCKET_WIDTH_MINUTES = 60


def floor_to_bucket_start(moment):
    """Floor a tz-aware datetime to the top of its hour.

    Flooring to a 1-hour boundary is timezone-representation-independent (the top
    of an hour is the same instant regardless of which tz the datetime is
    displayed in), unlike jg's original 4-hour flooring which needed a local-day
    anchor. No timezone conversion is needed here.
    """
    return moment.replace(minute=0, second=0, microsecond=0)


class ActivityBucket(models.Model):
    """One user's accumulated presence within one hour, scoped to a consumer-owned object.

    The scope is a plain (content_type, object_id) pair, exactly mirroring
    MessagingScope's generic-FK shape (django_core_micha.messaging.models:46-68) —
    dcm never learns what the scope object *is*. `app_key` additionally
    disambiguates the same (content_type, object_id) pair reused by two different
    consuming apps (unlikely in practice since content types are Python-class-scoped
    per install, but cheap to guard and mirrors MessagingScope's own uniqueness
    shape, which includes its app FK).
    """

    app_key = models.CharField(max_length=64)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    scope_object = GenericForeignKey("content_type", "object_id")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    bucket_start = models.DateTimeField()
    active_seconds = models.PositiveIntegerField(default=0)
    last_ping_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["app_key", "content_type", "object_id", "user", "bucket_start"],
                name="activity_bucket_scope_user_bucket_uniq",
            ),
        ]
        indexes = [
            # The read pattern is always scope + bucket range (the query endpoint's
            # aggregation groups over exactly these fields).
            models.Index(
                fields=["app_key", "content_type", "object_id", "bucket_start"],
                name="activity_bucket_scope_range_idx",
            ),
        ]
