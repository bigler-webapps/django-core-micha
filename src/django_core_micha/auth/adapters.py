from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from allauth.account.adapter import DefaultAccountAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings
from django_core_micha.auth.security import get_security_level, set_security_level
import logging

logger = logging.getLogger(__name__)

class InvitationOnlySocialAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        User = get_user_model()
        email = sociallogin.user.email
        if not email:
            return False
        user_qs = User.objects.filter(email__iexact=email)
        if not user_qs.exists():
            return False

        # S7 hardcoded defense-in-depth: social auto-connect is only allowed
        # when the target user has a verified EmailAddress entry. This closes
        # the pre-squat + social-takeover path. Combined with the pending-token
        # registration flow (S13), unconfirmed accounts no longer exist in DB
        # in the first place, but this check stays as belt-and-braces.
        from allauth.account.models import EmailAddress as _EmailAddress
        verified = _EmailAddress.objects.filter(
            user__in=user_qs, email__iexact=email, verified=True
        ).exists()
        return verified

class CoreAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        """
        Generic allauth signup endpoints are intentionally closed.

        Projects must route self-signup through the policy-aware register_request
        flow so access-code, domain and QR requirements are enforced centrally.
        """
        return False

    def login(self, request, user):
        response = super().login(request, user)

        if request.resolver_match:
            view_name = request.resolver_match.view_name or ""
            logger.debug(
                "CoreAccountAdapter.login via %s (before=%s)",
                view_name,
                get_security_level(request),
            )

            current = get_security_level(request)

            # 1) Reiner WebAuthn-Login -> immer strong
            if "webauthn" in view_name and ("login" in view_name or "authenticate" in view_name):
                set_security_level(request, "strong")

            # 2) 2FA-/MFA-Authentifizierung (z.B. TOTP, Recovery) -> ebenfalls strong
            elif "2fa" in view_name or "mfa" in view_name:
                set_security_level(request, "strong")

            # 3) Sonst: Standard-Level (typischerweise "basic"),
            #    aber nur, wenn nicht bereits strong/recovery gesetzt wurde.
            else:
                if current not in ("strong", "recovery"):
                    set_security_level(request, getattr(settings, "SECURITY_DEFAULT_LEVEL", "basic"))

            logger.debug(
                "CoreAccountAdapter.login after: auth_level=%s",
                get_security_level(request),
            )

        return response

class CoreMFAAdapter(DefaultMFAAdapter):
    def on_authentication_success(self, request, user, **kwargs):
        # Successful MFA implies strong session security
        if getattr(request, "user", None) and request.user.is_authenticated:
            set_security_level(request, "strong")
        return super().on_authentication_success(request, user, **kwargs)
