# src/django_core_micha/auth/signals.py

import logging
from django.conf import settings
from django.db.models.signals import pre_save
from django.dispatch import Signal, receiver
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from allauth.account.models import EmailAddress
from allauth.account.signals import (
    email_added,
    email_confirmed,
    email_removed,
    password_changed,
    password_reset,
    password_set,
)
from allauth.socialaccount.signals import (
    pre_social_login,
    social_account_added,
    social_account_removed,
    social_account_updated,
)

from ._audit_helpers import _client_ip, _credential_hash, _session_key_digest, _ua_family

logger = logging.getLogger(__name__)


registration_completed = Signal()


def _request_meta(request) -> dict:
    return {
        "ip": _client_ip(request),
        "user_agent_family": _ua_family(request),
        "session_digest": _session_key_digest(
            getattr(getattr(request, "session", None), "session_key", None)
        ),
    }


def _create_auth_audit_event(actor, event_type: str, metadata: dict) -> None:
    from django_core_micha.auditlog.models import AuditEvent
    try:
        AuditEvent.objects.create(
            actor=actor,
            event_type=event_type,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to write auth AuditEvent for %s", event_type)


# ---------------------------------------------------------------------------
# django.contrib.auth signals
# ---------------------------------------------------------------------------

@receiver(user_logged_in)
def mark_profile_not_new_after_first_login(sender, request, user, **kwargs):
    """
    Marks profile.is_new=False on first successful login.
    Central server-side behavior so frontends do not need to patch is_new.
    """
    profile = getattr(user, "profile", None)
    if profile and hasattr(profile, "is_new") and profile.is_new:
        profile.is_new = False
        profile.save(update_fields=["is_new"])
        logger.info("Marked is_new=False after first login for user %s", getattr(user, "email", user.pk))

    _create_auth_audit_event(
        actor=user,
        event_type="users.user.logged_in",
        metadata=_request_meta(request),
    )


@receiver(user_logged_out)
def _on_user_logged_out(sender, request, user, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.logged_out",
        metadata=_request_meta(request),
    )


@receiver(user_login_failed)
def _on_user_login_failed(sender, credentials, request, **kwargs):
    meta = _request_meta(request)
    meta["credential_hash"] = _credential_hash(credentials)
    _create_auth_audit_event(
        actor=None,
        event_type="users.user.login_failed",
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# allauth.account password signals
# ---------------------------------------------------------------------------

@receiver(password_changed)
def _on_password_changed(sender, request, user, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.password_changed",
        metadata=_request_meta(request),
    )


@receiver(password_set)
def _on_password_set(sender, request, user, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.password_set",
        metadata=_request_meta(request),
    )


@receiver(password_reset)
def _on_password_reset(sender, request, user, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.password_reset",
        metadata=_request_meta(request),
    )


# ---------------------------------------------------------------------------
# allauth.account email signals
# ---------------------------------------------------------------------------

@receiver(email_confirmed)
def _on_email_confirmed(sender, request, email_address, **kwargs):
    _create_auth_audit_event(
        actor=email_address.user,
        event_type="users.user.email.confirmed",
        metadata={
            **_request_meta(request),
            "email_domain": email_address.email.split("@")[-1] if "@" in email_address.email else None,
        },
    )


@receiver(email_added)
def _on_email_added(sender, request, user, email_address, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.email.added",
        metadata={
            **_request_meta(request),
            "email_domain": email_address.email.split("@")[-1] if "@" in email_address.email else None,
        },
    )


@receiver(email_removed)
def _on_email_removed(sender, request, user, email_address, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.email.removed",
        metadata={
            **_request_meta(request),
            "email_domain": email_address.email.split("@")[-1] if "@" in email_address.email else None,
        },
    )


# ---------------------------------------------------------------------------
# allauth.mfa signals — connected lazily from AppConfig.ready() so that this
# module can be imported even when allauth.mfa is not in INSTALLED_APPS.
# ---------------------------------------------------------------------------

def _on_authenticator_added(sender, request, user, authenticator, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.mfa.authenticator_added",
        metadata={
            **_request_meta(request),
            "authenticator_type": authenticator.type,
        },
    )


def _on_authenticator_removed(sender, request, user, authenticator, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.mfa.authenticator_removed",
        metadata={
            **_request_meta(request),
            "authenticator_type": authenticator.type,
        },
    )


def _on_authenticator_reset(sender, request, user, authenticator, **kwargs):
    _create_auth_audit_event(
        actor=user,
        event_type="users.user.mfa.authenticator_reset",
        metadata={
            **_request_meta(request),
            "authenticator_type": authenticator.type,
        },
    )


def connect_mfa_signals():
    """Connect MFA signal receivers. Called from AppConfig.ready() after allauth.mfa is ready."""
    from allauth.mfa.signals import authenticator_added, authenticator_removed, authenticator_reset
    authenticator_added.connect(_on_authenticator_added, dispatch_uid="dcm_on_authenticator_added")
    authenticator_removed.connect(_on_authenticator_removed, dispatch_uid="dcm_on_authenticator_removed")
    authenticator_reset.connect(_on_authenticator_reset, dispatch_uid="dcm_on_authenticator_reset")


# ---------------------------------------------------------------------------
# allauth.socialaccount signals
# ---------------------------------------------------------------------------

@receiver(social_account_added)
def _on_social_account_added(sender, request, sociallogin, **kwargs):
    _create_auth_audit_event(
        actor=sociallogin.user,
        event_type="users.user.social.added",
        metadata={
            **_request_meta(request),
            "provider": sociallogin.account.provider,
            "uid": sociallogin.account.uid,
        },
    )


@receiver(social_account_removed)
def _on_social_account_removed(sender, request, socialaccount, **kwargs):
    _create_auth_audit_event(
        actor=socialaccount.user,
        event_type="users.user.social.removed",
        metadata={
            **_request_meta(request),
            "provider": socialaccount.provider,
            "uid": socialaccount.uid,
        },
    )


@receiver(social_account_updated)
def _on_social_account_updated(sender, request, sociallogin, **kwargs):
    _create_auth_audit_event(
        actor=sociallogin.user,
        event_type="users.user.social.updated",
        metadata={
            **_request_meta(request),
            "provider": sociallogin.account.provider,
            "uid": sociallogin.account.uid,
        },
    )


# ---------------------------------------------------------------------------
# Existing internal signals
# ---------------------------------------------------------------------------

@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def prevent_password_wipe(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_user = sender.objects.get(pk=instance.pk)
            has_old_pw = old_user.password and not old_user.password.startswith('!')
            is_wiping_pw = not instance.password or instance.password.startswith('!')

            if has_old_pw and is_wiping_pw:
                instance.password = old_user.password
                logger.info(f"Prevented password wipe for user {instance.email}")
        except sender.DoesNotExist:
            pass


@receiver(pre_social_login)
def force_auto_connect_on_email_match(sender, request, sociallogin, **kwargs):
    """
    Automatically links a social account to an existing local user if the emails match.
    SECURITY FIX: Only links if the local email address is explicitly verified.
    This prevents pre-account takeover attacks where an attacker creates an unverified
    account with a victim's email.
    """
    if sociallogin.is_existing or not sociallogin.email_addresses:
        return

    social_email = sociallogin.email_addresses[0].email
    User = get_user_model()

    try:
        user = User.objects.get(email__iexact=social_email)

        is_local_verified = EmailAddress.objects.filter(
            user=user,
            email__iexact=social_email,
            verified=True
        ).exists()

        if is_local_verified:
            sociallogin.connect(request, user)
            logger.info(f"Auto-connected social account for verified user {social_email}")
        else:
            logger.warning(
                f"Skipped auto-connect for {social_email}: Local account exists but email is not verified. "
                "Possible pre-account takeover attempt or stale unverified account."
            )

    except User.DoesNotExist:
        pass
