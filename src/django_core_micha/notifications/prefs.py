"""The stable notification-preference seam."""
from .models import NotificationPreference


def is_channel_enabled(user, category, channel) -> bool:
    """NOTIF-3 replaces the backing with the category x channel matrix; signature is stable."""

    del category  # Category-aware preferences are intentionally deferred to NOTIF-3.
    if channel not in {"email", "push"}:
        return True

    preference = NotificationPreference.objects.filter(user=user).values(
        "email_opt_in", "push_opt_in"
    ).first()
    if preference is None:
        return False
    return bool(preference[f"{channel}_opt_in"])
