from django.contrib.auth import get_user_model
from django.db import models


class Widget(models.Model):
    name = models.CharField(max_length=100)
    secret = models.CharField(max_length=100, blank=True)
    updated_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        app_label = "testapp"


class Gadget(models.Model):
    title = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class AuthPolicyStub(models.Model):
    """Minimal stand-in for a consuming app's concrete AUTH_POLICY_MODEL."""

    allow_admin_invite = models.BooleanField(default=True)
    allow_self_signup_access_code = models.BooleanField(default=False)
    allow_self_signup_open = models.BooleanField(default=False)
    allow_self_signup_email_domain = models.BooleanField(default=False)
    allow_self_signup_qr = models.BooleanField(default=False)
    allowed_email_domains = models.JSONField(default=list)
    required_auth_factor_count = models.IntegerField(default=1)
    admin_required_auth_factor_count = models.IntegerField(default=1)
    signup_qr_expiry_days = models.IntegerField(default=90)
    access_code_single_use = models.BooleanField(default=False)

    class Meta:
        app_label = "testapp"


class AuthPolicyStubAlt(models.Model):
    """Second, distinct swappable-model target for cross-model cache-key tests."""

    allow_admin_invite = models.BooleanField(default=True)
    allow_self_signup_access_code = models.BooleanField(default=False)
    allow_self_signup_open = models.BooleanField(default=False)
    allow_self_signup_email_domain = models.BooleanField(default=False)
    allow_self_signup_qr = models.BooleanField(default=False)
    allowed_email_domains = models.JSONField(default=list)
    required_auth_factor_count = models.IntegerField(default=1)
    admin_required_auth_factor_count = models.IntegerField(default=1)
    signup_qr_expiry_days = models.IntegerField(default=90)
    access_code_single_use = models.BooleanField(default=False)

    class Meta:
        app_label = "testapp"
