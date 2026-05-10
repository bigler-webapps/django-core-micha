from django.conf import settings
from django.core.mail import EmailMessage
import logging

from django_core_micha.emails import email_texts

logger = logging.getLogger(__name__)

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
        from_email=from_email,  # None → DEFAULT_FROM_EMAIL
        to=[user.email],
        headers=headers,
    )
    email.send(fail_silently=False)