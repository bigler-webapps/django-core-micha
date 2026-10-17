"""Require an enrolled MFA factor and an MFA-authenticated session for admin.

The Django admin is a well-known target for credential stuffing and session
hijacking. In non-local environments, authenticated superusers and staff must
both have a real allauth authenticator enrolled and must have completed its
MFA step in the current session. Recovery codes alone do not count.

The check is skipped when ``ADMIN_MFA_REQUIRED=False``, for non-admin paths,
in local environments (``IS_LOCAL=True``), and for anonymous requests.
Non-staff users who are not superusers are left to Django admin's own access
gate.

Operators must log in through the normal allauth login with a second factor
before entering the admin. On a fresh deployment, use the normal login to
authenticate, enroll a real factor through the account MFA page, then log in
again with that factor before visiting ``/admin/``. This middleware does not
provide an admin password-login bootstrap path.

**Note on staging:** MFA enforcement applies in staging as in production;
``IS_LOCAL`` is only true for ``ENV_TYPE=local``.

Audit-Trail: webapp-management/SECURITY_FINDINGS.md S267.
"""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseForbidden


def session_used_mfa(request) -> bool:
    """Return whether this session records a non-recovery MFA login.

    allauth stores authentication records in the session. Treat unexpected
    session data, malformed records, and import/runtime failures as no MFA.
    """
    try:
        from allauth.account.authentication import get_authentication_records

        records = get_authentication_records(request)
        if not isinstance(records, (list, tuple)):
            return False
        return any(
            isinstance(record, dict)
            and record.get("method") == "mfa"
            and record.get("type") in ("totp", "webauthn")  # an allowlist: unknown or missing types fail closed
            for record in records
        )
    except Exception:
        return False


class AdminMfaRequiredMiddleware:
    """Enforce enrolled and session-used MFA for admin access."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_mfa_check(request):
            try:
                enrolled = self._has_mfa(request.user)
            except Exception:
                # If allauth or its authenticator store is unavailable, fail
                # closed instead of letting an uncertain admin request pass.
                enrolled = False
            if not enrolled:
                return HttpResponseForbidden(
                    "Admin access requires an enrolled real MFA factor "
                    "(TOTP or WebAuthn). Enroll one in your account settings."
                )
            if not session_used_mfa(request):
                return HttpResponseForbidden(
                    "This session was not authenticated with MFA. Log in again "
                    "through the normal login and complete your second factor."
                )
        return self.get_response(request)

    @staticmethod
    def _needs_mfa_check(request) -> bool:
        if not getattr(settings, "ADMIN_MFA_REQUIRED", True):
            return False
        if getattr(settings, "IS_LOCAL", False):
            return False
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if not (
            getattr(user, "is_superuser", False)
            or getattr(user, "is_staff", False)
        ):
            return False
        # Only superusers and staff reach the (comparatively expensive) URL resolution.
        return AdminMfaRequiredMiddleware._is_admin_request(request)

    @staticmethod
    def _is_admin_request(request) -> bool:
        """True when the request is routed to a Django admin site.

        ``path_info`` is relative to the script prefix (SCRIPT_NAME=/portal makes
        ``request.path`` "/portal/admin/"), which is what the URL conf routes on. The
        default ``/admin/`` prefix is always gated; in addition the URL is resolved, so an
        admin mounted at another prefix (``path("backend/admin/", admin.site.urls)``) or a
        custom ``AdminSite`` is gated too: every admin site's URLs carry the application
        namespace ``admin``. An unexpected resolver error fails closed (gated); an absent URL conf or
        route means nothing admin is reachable. Place this middleware AFTER any middleware that
        selects ``request.urlconf`` (a later swap of the URL conf cannot be seen from here).
        """
        if request.path_info.startswith("/admin/"):
            return True
        try:
            from django.core.exceptions import ImproperlyConfigured
            from django.urls import Resolver404, resolve

            match = resolve(request.path_info, urlconf=getattr(request, "urlconf", None))
        except (Resolver404, ImproperlyConfigured, AttributeError):
            return False  # no route, or no URL conf at all (nothing, hence no admin, can be routed)
        except Exception:
            return True
        return "admin" in (match.app_names or [])

    @staticmethod
    def _has_mfa(user) -> bool:
        # Keep allauth optional for projects that do not install MFA.
        from allauth.mfa.models import Authenticator

        return (
            Authenticator.objects.filter(user=user)
            .exclude(type=Authenticator.Type.RECOVERY_CODES)
            .exists()
        )
