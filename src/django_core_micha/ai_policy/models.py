from django.db import models


class AIModelPolicy(models.Model):
    """The last centrally applied policy for this deployment database."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    app_id = models.CharField(max_length=128)
    environment = models.CharField(max_length=128)
    revision = models.PositiveBigIntegerField()
    allowed = models.JSONField(default=dict)
    standard_source_id = models.CharField(max_length=192)
    standard_model_id = models.CharField(max_length=192)
    applied_at = models.DateTimeField(auto_now=True)
    last_refresh_at = models.DateTimeField(null=True, blank=True)


PolicyState = AIModelPolicy
