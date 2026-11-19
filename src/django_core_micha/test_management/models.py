import uuid

from django.db import models


class TestInstance(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    controller_token_digest = models.CharField(max_length=64)
    boot_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class TestRun(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RECOVERABLE = "recoverable", "Recoverable"
        RESET = "reset", "Reset"
        CLOSED = "closed", "Closed"

    handle = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    instance = models.ForeignKey(TestInstance, on_delete=models.CASCADE, related_name="runs")
    idempotency_key = models.CharField(max_length=200)
    baseline = models.JSONField(default=dict)
    adapter_state = models.JSONField(default=dict)
    recovery_reason = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "idempotency_key"],
                name="dcm_tm_run_instance_idempotency",
            ),
        ]


class TestAccount(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DELETED = "deleted", "Deleted"
        CLEANUP_FAILED = "cleanup_failed", "Cleanup failed"

    handle = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="accounts")
    idempotency_key = models.CharField(max_length=200)
    template = models.CharField(max_length=100)
    creation_fingerprint = models.CharField(max_length=64)
    requested_configuration = models.JSONField(default=dict)
    synthetic_identity = models.JSONField(default=dict)
    adapter_ref = models.CharField(max_length=255)
    user_ref = models.CharField(max_length=255, blank=True)
    effective_state = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "idempotency_key"],
                name="dcm_tm_account_run_idempotency",
            ),
        ]


class TestFixture(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DELETED = "deleted", "Deleted"
        CLEANUP_FAILED = "cleanup_failed", "Cleanup failed"

    handle = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="fixtures")
    idempotency_key = models.CharField(max_length=200)
    name = models.CharField(max_length=100)
    parameters = models.JSONField(default=dict)
    adapter_ref = models.CharField(max_length=255)
    state = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "idempotency_key"],
                name="dcm_tm_fixture_run_idempotency",
            ),
        ]


class TestMailMessage(models.Model):
    class Status(models.TextChoices):
        CAPTURED = "captured", "Captured"
        CLAIMED = "claimed", "Claimed"
        DISCARDED = "discarded", "Discarded"

    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="mail_messages")
    account = models.ForeignKey(TestAccount, on_delete=models.CASCADE, related_name="mail_messages")
    handle = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=32, default="password_reset")
    link = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CAPTURED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
