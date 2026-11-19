from django.apps import apps
from django.conf import settings
from django.core.mail import EmailMessage
import logging

from django_core_micha.emails import email_texts

logger = logging.getLogger(__name__)

def send_pending_registration_email(*, email: str, url: str) -> None:
    """S13: deliver the confirm-link for a pending-registration token.

    Unlike ``send_invite_or_reset_email`` this does not require a DB ``User``
    instance — the user does not exist yet at this point in the flow.
    """
    subject, body = email_texts.render_pending_registration_email(email, url)

    if getattr(settings, "ENV_TYPE", "") == "local":
        logger.info("[LOCAL] Pending-Registration-Mail an %s: %s", email, url)
        return

    from_email = getattr(settings, "INVITATIONS_FROM_EMAIL", None)
    reply_to = getattr(settings, "INVITATIONS_REPLY_TO", None)

    headers = {}
    if reply_to:
        headers["Reply-To"] = reply_to

    message = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[email],
        headers=headers,
    )
    message.send(fail_silently=False)


def send_invite_or_reset_email(*, user, url, is_new_user: bool) -> None:
    """
    Generic helper: nimmt User + Link, holt sich Texte aus dem emails Modul
    und schickt die Mail raus.
    """
    if is_new_user:
        subject, body = email_texts.render_invite_email(user, url)
    else:
        subject, body = email_texts.render_reset_email(user, url)

    if getattr(settings, "ENV_TYPE", "") == "local":
        if not is_new_user:
            capture_enabled = (
                getattr(settings, "TEST_MANAGEMENT_ENABLED", False) is True
                and apps.is_installed("django_core_micha.test_management")
            )
            try:
                from django_core_micha.test_management.mail_capture import capture_password_reset
                captured = capture_password_reset(user, url, body=body)
            except Exception:
                logger.warning("test_management_mail_capture_failed")
                captured = False
            if captured:
                logger.info("[LOCAL] Password reset message captured")
                return
            if capture_enabled:
                # In an isolated run, never fall back to logging the private reset URL.
                return
        logger.info(
            "[LOCAL] Invite/Reset-Mail an %s: %s",
            user.email,
            url,
        )
        return

    from_email = getattr(settings, "INVITATIONS_FROM_EMAIL", None)
    reply_to = getattr(settings, "INVITATIONS_REPLY_TO", None)

    headers = {}
    if reply_to:
        headers["Reply-To"] = reply_to

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,  # None DEFAULT_FROM_EMAIL
        to=[user.email],
        headers=headers,
    )
    email.send(fail_silently=False)
