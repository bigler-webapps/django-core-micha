from django.conf import settings
from django.db import models


class ReceiptConsent(models.Model):
    """Standing receipt-recognition consent and its grant/revoke timestamps."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="receipt_consent")
    granted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=40, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

