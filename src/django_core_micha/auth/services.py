import logging
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from django_core_micha.auth.access import can_user_authenticate
from django_core_micha.auth.recovery import RecoveryRequest, _compute_token_hmac
from django_core_micha.auth.security import set_security_level
from django_core_micha.emails import email_texts
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

# --- Email Helper ---
def send_recovery_link_to_user(rr: RecoveryRequest, recovery_url: str):
    user = rr.user
    if not user or not user.email:
        return
    subject, message = email_texts.render_recovery_email(user, recovery_url)
    
    if getattr(settings, "ENV_TYPE", "") == "local":
        logger.info(f"[LOCAL] Recovery-Mail to {user.email}: {recovery_url}")
        return

    send_mail(subject, message, getattr(settings, "DEFAULT_FROM_EMAIL", None), [user.email], fail_silently=True)

# --- Business Logic ---

def approve_recovery_request(request, recovery_request: RecoveryRequest, support_note: str) -> None:
    """Approve the request, mark it APPROVED, and email the recovery link
    to the user.

    S164: the link is intentionally NOT returned. The support agent does
    not need it — only the email recipient does — and any return path
    would risk re-introducing the token-in-API-response leak.
    """
    # 1. Build the recovery URL for the email body only.
    path = reverse("mfa-recovery-complete", args=[recovery_request.token])
    recovery_url = request.build_absolute_uri(path)

    # 2. Update DB
    recovery_request.mark_resolved(
        RecoveryRequest.Status.APPROVED,
        by=request.user,
        note=support_note,
    )

    # 3. Send Email
    send_recovery_link_to_user(recovery_request, recovery_url)

def reject_recovery_request(request, recovery_request: RecoveryRequest, support_note: str):
    recovery_request.mark_resolved(
        RecoveryRequest.Status.REJECTED,
        by=request.user,
        note=support_note,
    )

def perform_recovery_login(request, identifier: str, password: str, token: str):
    """
    Validates token and credentials, performs login, and closes the request.
    Raises AuthenticationFailed if anything is wrong.
    """
    # 1. Validate Token via HMAC-Lookup (S51: constant-time, kein plaintext-
    # Compare in der DB-Engine). Pattern analog `validate_access_code_or_error`
    # in `invitations/access_codes.py` (S18). Plaintext-Token bleibt im
    # `token`-Feld erhalten für Admin-Sichtbarkeit + Email-Link-Generierung.
    token_hmac = _compute_token_hmac(token)
    rr = (
        RecoveryRequest.objects.select_related("user")
        .filter(token_hmac=token_hmac, status=RecoveryRequest.Status.APPROVED)
        .first()
    )
    if rr is None:
        raise AuthenticationFailed(code="Auth.RECOVERY_TOKEN_INVALID")

    if not rr.is_active():
        # Queryset above already filtered status=APPROVED, so this branch is
        # only reachable when the approved token has expired by time. No
        # PENDING handling needed here; if the lookup is ever widened, the
        # caller must reintroduce the status guard.
        rr.mark_resolved(RecoveryRequest.Status.EXPIRED)
        raise AuthenticationFailed(code="Auth.RECOVERY_TOKEN_EXPIRED")

    # 2. Validate User & Password
    # Note: We check user existence & password securely to avoid timing attacks/leaks logic
    User = rr.user._meta.model
    try:
        user = User.objects.get(email__iexact=identifier)
    except User.DoesNotExist:
        raise AuthenticationFailed(code="Auth.INVALID_CREDENTIALS")

    if user.pk != rr.user_id or not user.check_password(password):
        raise AuthenticationFailed(code="Auth.INVALID_CREDENTIALS")
    if not can_user_authenticate(user):
        raise AuthenticationFailed(code="Auth.INVALID_CREDENTIALS")

    # 3. Perform Django Login
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    # 4. Set Session Security & Finalize
    set_security_level(request, "basic")
    rr.mark_completed()
