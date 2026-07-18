"""The stable notification-preference seam."""
from .models import (
    NotificationCategoryChannelPreference,
    NotificationChannelDefault,
    NotificationPreference,
)


def is_channel_enabled(user, category, channel) -> bool:
    """Resolve a channel's enabled state through four precedence tiers.

    1. A per-(category, channel) ``NotificationCategoryChannelPreference`` override, if set.
    2. A per-channel ``NotificationChannelDefault``, if the user (or a future preferences UI)
       has explicitly set one via ``NotificationChannelDefault.set_channel_default``.
    3. For email/push: the LIVE legacy ``NotificationPreference.email_opt_in``/``push_opt_in``
       boolean, if a preference row exists. This is deliberately live (not seeded once) so that
       the still-active ``NotificationPreferenceView`` endpoint keeps working for any user who
       has not yet been given an explicit channel default (i.e. everyone, until a preferences UI
       starts calling tier 2) — there is no one-time data migration seeding tier 2 from tier 3
       precisely to avoid freezing a stale snapshot ahead of future live legacy toggles.
    4. The hardcoded built-in default: email/push default False (opt-in), all other channels
       (chip/todo/popup) default True.
    """

    category_override = (
        NotificationCategoryChannelPreference.objects.filter(
            user=user,
            category=category,
            channel=channel,
        )
        .values_list("enabled", flat=True)
        .first()
    )
    if category_override is not None:
        return bool(category_override)

    channel_default = (
        NotificationChannelDefault.objects.filter(user=user, channel=channel)
        .values_list("enabled", flat=True)
        .first()
    )
    if channel_default is not None:
        return bool(channel_default)

    if channel in {"email", "push"}:
        legacy_preference = NotificationPreference.objects.filter(user=user).values_list(
            f"{channel}_opt_in", flat=True
        ).first()
        if legacy_preference is not None:
            return bool(legacy_preference)

    if channel not in {"email", "push"}:
        return True
    return False
