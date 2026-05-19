# src/django_core_micha/auth/models.py
from django.db import models
from django.conf import settings
from .roles import get_role_choices, get_default_role_code # <--- Import aus roles.py
from .policy import AUTH_FACTOR_SINGLE, AUTH_FACTOR_TWO, DEFAULT_SIGNUP_QR_EXPIRY_DAYS
import uuid

class AbstractUserProfile(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="profile"
    )
    
    # Hier nutzen wir die zentralen Funktionen
    role = models.CharField(
        max_length=64,
        choices=get_role_choices(),      # <--- Neu
        default=get_default_role_code    # <--- Existierend
    )

    language = models.CharField(max_length=10, default="en")
    is_new = models.BooleanField(default=True)
    is_invited = models.BooleanField(default=False)
    accepted_privacy_statement = models.BooleanField(default=False)
    accepted_convenience_cookies = models.BooleanField(default=False)

    is_support_agent = models.BooleanField(default=False)
    
    support_contact = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="supported_users",
        on_delete=models.SET_NULL,
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.user.email} ({self.role})"


class AbstractAuthPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    allow_admin_invite = models.BooleanField(default=True)
    allow_self_signup_access_code = models.BooleanField(default=False)
    allow_self_signup_open = models.BooleanField(default=False)
    allow_self_signup_email_domain = models.BooleanField(default=False)
    allow_self_signup_qr = models.BooleanField(default=False)
    allowed_email_domains = models.JSONField(default=list, blank=True)
    required_auth_factor_count = models.PositiveSmallIntegerField(
        default=AUTH_FACTOR_SINGLE
    )
    admin_required_auth_factor_count = models.PositiveSmallIntegerField(
        default=AUTH_FACTOR_SINGLE
    )
    signup_qr_expiry_days = models.PositiveIntegerField(default=DEFAULT_SIGNUP_QR_EXPIRY_DAYS)
    # S18: per-app admin policy — when True, access-code redemptions are
    # consumed (single-use).
    access_code_single_use = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def clean(self):
        domains = []
        seen = set()
        for raw in self.allowed_email_domains or []:
            if not isinstance(raw, str):
                continue
            domain = raw.strip().lower().lstrip("@")
            if not domain or "@" in domain or domain in seen:
                continue
            seen.add(domain)
            domains.append(domain)
        self.allowed_email_domains = domains

        if int(self.required_auth_factor_count) not in (AUTH_FACTOR_SINGLE, AUTH_FACTOR_TWO):
            self.required_auth_factor_count = AUTH_FACTOR_SINGLE
        if int(self.admin_required_auth_factor_count) not in (AUTH_FACTOR_SINGLE, AUTH_FACTOR_TWO):
            self.admin_required_auth_factor_count = self.required_auth_factor_count

        try:
            self.signup_qr_expiry_days = max(1, int(self.signup_qr_expiry_days))
        except (TypeError, ValueError):
            self.signup_qr_expiry_days = DEFAULT_SIGNUP_QR_EXPIRY_DAYS

    def save(self, *args, **kwargs):
        self.id = 1
        self.clean()
        return super().save(*args, **kwargs)


class AbstractSignupQrToken(models.Model):
    """S30: DB-persistent QR signup token with use-counter.

    Apps that want true single-use / capped-redemption semantics provide a
    concrete subclass and set ``settings.SIGNUP_QR_TOKEN_MODEL = "app.Model"``.

    Apps without ``SIGNUP_QR_TOKEN_MODEL`` configured keep the legacy stateless
    behaviour (signed token only, unlimited use within payload expiry).
    """

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    mode = models.CharField(max_length=32)
    registration_context = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    max_redemptions = models.PositiveSmallIntegerField(default=1)
    use_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SignupQrToken({self.token_hash[:8]}…, {self.use_count}/{self.max_redemptions})"
