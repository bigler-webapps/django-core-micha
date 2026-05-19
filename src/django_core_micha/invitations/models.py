# django_core_micha/invitations/models.py
import hashlib
import hmac

from django.db import models
from django.conf import settings


def _compute_code_hmac(code: str) -> str:
    """HMAC-SHA256 over the access code using SECRET_KEY as the key.

    S18: enables constant-time lookup by hash instead of plaintext compare.
    Plaintext code is still stored in `code` for admin visibility / sharing.
    """
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, code.encode("utf-8"), hashlib.sha256).hexdigest()


class AccessCode(models.Model):
    code = models.CharField(max_length=64, unique=True)
    # S18: HMAC of `code` for constant-time lookup. Set automatically by save().
    code_hmac = models.CharField(max_length=64, db_index=True, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_access_codes",
    )

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Always keep code_hmac in sync with code.
        self.code_hmac = _compute_code_hmac(self.code or "")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.code
