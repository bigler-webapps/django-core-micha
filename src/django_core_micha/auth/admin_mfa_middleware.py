"""S19 — Require MFA enrollment for Django admin access in non-local envs.

The Django admin URL (``/admin/``) is a well-known target for credential-stuffing
and session-hijacking attacks. Superuser passwords compromised elsewhere give
an attacker full data-access via admin without any additional barrier.

This middleware enforces that any authenticated superuser hitting
``/admin/*`` MUST have at least one **real** ``allauth.mfa.models.Authenticator``
row (TOTP or WebAuthn) — Recovery-codes alone don't count, since they're a
fallback factor, not a real second factor. Otherwise the request is rejected
with 403.

The check is skipped in:

- local environments (``IS_LOCAL=True``)
- unauthenticated requests (Django admin's login flow handles those)
- non-superuser requests (Django admin gates those itself)
- when ``ADMIN_MFA_REQUIRED=False`` (explicit opt-out)

**Note on staging:** the middleware enforces MFA in staging just as in
production (``IS_LOCAL`` is only ``True`` for ``ENV_TYPE=local``). Operators
provisioning a new tenant must enroll MFA in `/api/accounts/mfa/...` before
attempting admin login.

**Bootstrap flow** (first superuser on fresh deployment):
1. `/admin/login/` — passes (unauthenticated middleware skips)
2. After login, navigating into `/admin/` 403
3. While still authenticated, visit `/api/accounts/mfa/totp/activate/` to
   enroll TOTP (or the corresponding allauth-mfa URL)
4. Retry `/admin/` — passes

Audit-Trail: `webapp-management/SECURITY_FINDINGS.md` S19.
"""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseForbidden


class AdminMfaRequiredMiddleware:
    """Enforce MFA enrollment for Django admin access in non-local envs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_mfa_check(request) and not self._has_mfa(request.user):
            return HttpResponseForbidden(
                "Admin access requires a real MFA factor (TOTP or WebAuthn) "
                "enrolled on your account. Enroll an authenticator in your "
                "account settings, then retry. Recovery codes alone do not "
                "satisfy this requirement."
            )
        return self.get_response(request)

    @staticmethod
    def _needs_mfa_check(request) -> bool:
        # Opt-out switch (default: enforce).
        if not getattr(settings, "ADMIN_MFA_REQUIRED", True):
            return False
        # Only Django admin URLs.
        if not request.path.startswith("/admin/"):
            return False
        # Local dev bypass — devs typically don't have MFA enrolled.
        if getattr(settings, "IS_LOCAL", False):
            return False
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        # Non-superusers can't reach admin anyway; Django gates that.
        if not getattr(user, "is_superuser", False):
            return False
        return True

    @staticmethod
    def _has_mfa(user) -> bool:
        # Late import: avoid circular-import at module load time and only pay
        # the Authenticator-import cost when the middleware actually fires.
        from allauth.mfa.models import Authenticator
        # Recovery-codes alone are a fallback, NOT a real second factor.
        # Exclude them so a superuser with only recovery-codes-state (e.g. after
        # disabling TOTP without cleanup) doesn't bypass the gate. Mirrors
        # allauth's own `can_generate_recovery_codes` exclusion pattern.
        return (
            Authenticator.objects.filter(user=user)
            .exclude(type=Authenticator.Type.RECOVERY_CODES)
            .exists()
        )
