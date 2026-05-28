from django.contrib.auth import get_user_model
from django.db import models


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditlog_events",
    )
    event_type = models.CharField(max_length=120)
    event_code = models.CharField(max_length=120, blank=True)
    message = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "django_core_micha_auditlog"
        ordering = ["-created_at", "-id"]
        indexes = [
            # Index name kept ≤30 chars to satisfy models.E034 (Django default
            # max identifier length). "evtype" = short for "event_type".
            models.Index(fields=["event_type", "created_at"], name="auditlog_evtype_created_idx"),
            models.Index(fields=["actor", "created_at"], name="auditlog_actor_created_idx"),
        ]

    def __str__(self):
        return f"AuditEvent {self.event_type} ({self.id})"
