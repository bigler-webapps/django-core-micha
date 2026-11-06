from django.db import models
from django.utils import timezone


class PrivacyReceipt(models.Model):
    receipt_id = models.CharField(max_length=64, unique=True)
    app = models.CharField(max_length=80)
    site = models.CharField(max_length=80)
    created_at = models.DateTimeField(default=timezone.now)
    seq = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "dcm_privacy_receipt"
        indexes = [models.Index(fields=("app", "site"), name="dcm_priv_receipt_scope_idx")]


class PrivacyChoice(models.Model):
    GRANTED = "granted"
    REFUSED = "refused"
    STATE_CHOICES = ((GRANTED, "Granted"), (REFUSED, "Refused"))

    receipt = models.ForeignKey(PrivacyReceipt, on_delete=models.CASCADE, related_name="choices")
    purpose_id = models.CharField(max_length=41)
    purpose_revision = models.PositiveIntegerField()
    state = models.CharField(max_length=8, choices=STATE_CHOICES)
    effective_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "dcm_privacy_choice"
        constraints = [
            models.UniqueConstraint(fields=("receipt", "purpose_id"), name="dcm_priv_choice_unique")
        ]
        indexes = [models.Index(fields=("expires_at",), name="dcm_priv_choice_expiry_idx")]


class PrivacyDecision(models.Model):
    GRANTED = "granted"
    REFUSED = "refused"
    WITHDRAWN = "withdrawn"
    KIND_CHOICES = (
        (GRANTED, "Granted"),
        (REFUSED, "Refused"),
        (WITHDRAWN, "Withdrawn"),
    )
    CHANNEL_CHOICES = (("banner", "Banner"), ("settings", "Settings"), ("other", "Other"))

    receipt = models.ForeignKey(PrivacyReceipt, on_delete=models.CASCADE, related_name="decisions")
    purpose_id = models.CharField(max_length=41)
    purpose_revision = models.PositiveIntegerField()
    kind = models.CharField(max_length=9, choices=KIND_CHOICES)
    seq = models.PositiveIntegerField()
    decided_at = models.DateTimeField(default=timezone.now)
    locale = models.CharField(max_length=5)
    notice_version = models.CharField(max_length=64)
    channel = models.CharField(max_length=8, choices=CHANNEL_CHOICES)
    request_id = models.CharField(max_length=36)

    class Meta:
        db_table = "dcm_privacy_decision"
        constraints = [
            models.UniqueConstraint(
                fields=("receipt", "request_id", "purpose_id"),
                name="dcm_priv_dec_req_uniq",
            )
        ]
        indexes = [models.Index(fields=("decided_at",), name="dcm_priv_decision_time_idx")]
