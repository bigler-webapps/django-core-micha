# src/django_core_micha/auth/signals.py

import logging
from django.conf import settings
from django.db.models.signals import pre_save
from django.dispatch import Signal, receiver
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from allauth.socialaccount.signals import pre_social_login
from allauth.account.models import EmailAddress  # <--- NEW IMPORT

logger = logging.getLogger(__name__)


registration_completed = Signal()


@receiver(user_logged_in)
def mark_profile_not_new_after_first_login(sender, request, user, **kwargs):
    """
    Marks profile.is_new=False on first successful login.
    Central server-side behavior so frontends do not need to patch is_new.
    """
    profile = getattr(user, "profile", None)
    if not profile:
        return
    if not hasattr(profile, "is_new"):
        return
    if not profile.is_new:
        return

    profile.is_new = False
    profile.save(update_fields=["is_new"])
    logger.info("Marked is_new=False after first login for user %s", getattr(user, "email", user.pk))

@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def prevent_password_wipe(sender, instance, **kwargs):
    # ... (Code bleibt unverändert) ...
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
    # If already connected or provider gives no email, do nothing
    if sociallogin.is_existing or not sociallogin.email_addresses:
        return
    
    social_email = sociallogin.email_addresses[0].email
    User = get_user_model()

    try:
        user = User.objects.get(email__iexact=social_email)
        
        # --- SECURITY CHECK START ---
        # Check if the existing local user has verified this email address.
        # We query the allauth EmailAddress model directly.
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
        # --- SECURITY CHECK END ---

    except User.DoesNotExist:
        # Normal flow: User does not exist, allauth will proceed to signup
        pass
