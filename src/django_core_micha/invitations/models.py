# django_core_micha/invitations/models.py
from django.db import models
from django.conf import settings


class AccessCode(models.Model):
    code = models.CharField(max_length=64, unique=True)
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

    def __str__(self) -> str:
        return self.code
