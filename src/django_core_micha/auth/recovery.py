# django_core_micha/auth/recovery.py
import uuid
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone
import secrets

def generate_recovery_token() -> str:
    return secrets.token_urlsafe(32)

class RecoveryRequest(models.Model):
    """
    Represents a one-time MFA recovery request for a user.
    Created when the user clicks "I can't use any of these methods".
    A supporter can turn this into a recovery login link.
    """

    class Status(models.TextChoices):
        PENDING   = "pending", "Pending"
        APPROVED  = "approved", "Approved"
        REJECTED  = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"
        EXPIRED   = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_requests",
    )

    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_recovery_token,
        help_text="One-time token used in the recovery login URL.",
    )

    support_note = models.TextField(
        blank=True,
        default="",
        help_text="Reason provided by the support agent when approving or rejecting.",
    )

    message = models.TextField(
        blank=True,
        default="",
        help_text="Optional message provided by the user.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="resolved_recovery_requests",
        on_delete=models.SET_NULL,
    )

    @property
    def expires_at(self):
        ttl_minutes = int(getattr(settings, "RECOVERY_REQUEST_TTL_MINUTES", 30) or 30)
        ttl_minutes = max(ttl_minutes, 1)
        return self.created_at + timedelta(minutes=ttl_minutes)

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at



    def mark_resolved(self, new_status, by=None, note=None):
        self.status = new_status
        self.resolved_by = by
        self.resolved_at = timezone.now()
        update_fields = ["status", "resolved_by", "resolved_at"]
        if note is not None:
            self.support_note = note
            update_fields.append("support_note")
        self.save(update_fields=update_fields)

    def mark_completed(self):
        self.status = self.Status.COMPLETED
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_at"])

    def is_active(self) -> bool:
        if self.status not in {self.Status.PENDING, self.Status.APPROVED}:
            return False
        return not self.is_expired()

    def __str__(self) -> str:
        return f"RecoveryRequest({self.user_id}, {self.status})"
