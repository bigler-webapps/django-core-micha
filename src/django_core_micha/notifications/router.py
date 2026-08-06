"""Channel resolution for canonical notifications."""
from .models import PushSubscription
from .prefs import is_channel_enabled


def _is_technically_available(user, channel: str) -> bool:
    if channel == "email":
        return bool(getattr(user, "email", ""))
    if channel == "push":
        return PushSubscription.objects.filter(user=user).exists()
    return True


def resolve_channels(ntype, user, override=None) -> list[str]:
    """Resolve eligible, enabled channels for a user from a type's reach declaration.

    Overrides replace ``ntype.eligible_channels`` (itself derived from reach, NOTIF-26
    scope A) but can only narrow within it and can never bypass preferences. Critical
    types may retain a channel despite an opt-out, but only when it is technically
    available: email needs an address and push needs at least one ``PushSubscription``.

    NOTIF-26 scope C's bounded fallback -- an active type degrades to passive only if
    it also declares passive -- needs no separate branch here: ``chip`` is only ever in
    ``eligible_channels`` when the type is passive, and ``is_channel_enabled`` resolves
    it independently of email/push availability (its own default is opt-out True). An
    active-only type has no ``chip`` in scope at all, so a user with no usable active
    channel simply resolves to an empty list -- undeliverable, not degraded.
    """

    eligible = ntype.eligible_channels
    base = [channel for channel in (override if override is not None else eligible) if channel in eligible]
    effective = []
    for channel in base:
        enabled = is_channel_enabled(user, ntype.category, channel)
        force = ntype.critical and channel in eligible
        if enabled or (force and _is_technically_available(user, channel)):
            if channel not in effective:
                effective.append(channel)
    return effective
