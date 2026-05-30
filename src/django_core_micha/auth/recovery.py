# django_core_micha/auth/recovery.py
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone


def generate_recovery_token() -> str:
    return secrets.token_urlsafe(32)


def _compute_token_hmac(token: str) -> str:
    """HMAC-SHA256 over the recovery token using SECRET_KEY as the key.

    S51: enables constant-time lookup by hash instead of plaintext-token compare.
    Plaintext token is still stored in `token` for admin visibility / link emit.
    Pattern analog `invitations.models._compute_code_hmac` (S18).
    """
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, (token or "").encode("utf-8"), hashlib.sha256).hexdigest()

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

    # S51: HMAC of `token` for constant-time DB-level equality lookup. Kept
    # in sync via `save()` override below. See `_compute_token_hmac` docstring
    # for the rationale (analog to invitations.AccessCode.code_hmac / S18).
    token_hmac = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        default="",
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
    # Timestamp of the PENDING APPROVED transition. Anchor for the post-
    # approval TTL window (see `expires_at`) so an approved token is not
    # already expiring when the user opens the recovery email — the previous
    # `created_at + TTL` made tokens unusable for requests that sat in
    # PENDING close to the TTL. Set-once by `mark_resolved(APPROVED)`. May
    # be NULL on rows that reached APPROVED outside that write path (raw
    # `QuerySet.update`, admin bulk action, fixtures); `expires_at` falls
    # back to `created_at` in that case.
    approved_at = models.DateTimeField(null=True, blank=True)
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
        if self.status == self.Status.APPROVED and self.approved_at is not None:
            base = self.approved_at
        else:
            base = self.created_at
        return base + timedelta(minutes=ttl_minutes)

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at



    def mark_resolved(self, new_status, by=None, note=None):
        self.status = new_status
        self.resolved_by = by
        self.resolved_at = timezone.now()
        update_fields = ["status", "resolved_by", "resolved_at"]
        # Set-once: a re-approval on an already-APPROVED row must not reset
        # the TTL anchor (defense against admin bulk-action or a misuse path
        # that would silently extend the validity window).
        if new_status == self.Status.APPROVED and self.approved_at is None:
            self.approved_at = self.resolved_at
            update_fields.append("approved_at")
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

    def save(self, *args, **kwargs):
        # S51: keep token_hmac in sync with token on every save. Same pattern
        # as `invitations.models.AccessCode.save`.
        #
        # Note on `save(update_fields=[...])` callers (`mark_resolved`,
        # `mark_completed`): the in-memory `self.token_hmac` is recomputed
        # but NOT persisted because `token_hmac` isn't in their `update_fields`.
        # That is safe because those flows don't change `self.token`, so the
        # already-persisted hmac stays correct. The recomputation is wasted
        # work but not a desync — if a future flow DOES mutate `self.token`
        # alongside a partial save, the caller must include `"token_hmac"`
        # in `update_fields` explicitly.
        self.token_hmac = _compute_token_hmac(self.token or "")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"RecoveryRequest({self.user_id}, {self.status})"
