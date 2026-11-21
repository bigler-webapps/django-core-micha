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


_EDITABLE_CHANNEL_PAIRS: dict[tuple[str, str], str] = {}


def register_editable_channel_pair(category: str, channel: str, label_key: str) -> None:
    """Declare a pair offered by the host application, with its translation key."""
    if not isinstance(category, str) or not category or len(category) > 32:
        raise ValueError("Invalid category.")
    if channel not in {"email", "push", "chip", "todo", "popup"}:
        raise ValueError("Invalid channel.")
    if not isinstance(label_key, str) or not label_key:
        raise ValueError("A label key is required.")
    _EDITABLE_CHANNEL_PAIRS[(category, channel)] = label_key


def iter_editable_channel_pairs() -> dict[tuple[str, str], str]:
    """Return the declared pairs; no declarations means no editable pairs."""
    return dict(_EDITABLE_CHANNEL_PAIRS)


def is_digest_email_enabled(user) -> bool:
    """Resolve the personal preference for todo digest email, not eligibility.

    Callers must separately apply their recipient and transactional mail policies.
    """
    return is_channel_enabled(user, "todo", "email")
