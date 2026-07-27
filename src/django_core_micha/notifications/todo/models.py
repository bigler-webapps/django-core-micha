"""Persistent provider-derived todo overlays."""
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class TodoOverride(models.Model):
    """An enable/lead-time override applied by a provider-selected scope object."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    scope = GenericForeignKey("content_type", "object_id")
    type_key = models.CharField(max_length=64)
    enabled = models.BooleanField(default=True)
    lead_days_override = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "type_key"],
                name="uniq_todo_override_scope_type",
            ),
        ]
