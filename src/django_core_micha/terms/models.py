from django.conf import settings
from django.db import models
from django.utils import timezone


class TermsAcceptance(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="terms_acceptances")
    app = models.CharField(max_length=80)
    version = models.CharField(max_length=64)
    document_url = models.URLField(max_length=500)
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "app", "version"), name="dcm_terms_acceptance_unique")]
