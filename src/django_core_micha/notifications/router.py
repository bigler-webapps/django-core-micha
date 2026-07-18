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
    """Resolve eligible, enabled channels for a user.

    Overrides replace the type defaults but can only narrow to eligible channels and
    can never bypass preferences.  Critical types may retain a selected default
    channel despite an opt-out, but only when that channel is technically available:
    email needs an address and push needs at least one ``PushSubscription``.
    """

    base = override if override is not None else ntype.default_channels
    eligible = [channel for channel in base if channel in ntype.eligible_channels]
    effective = []
    for channel in eligible:
        enabled = is_channel_enabled(user, ntype.category, channel)
        force = ntype.critical and channel in ntype.default_channels
        if enabled or (force and _is_technically_available(user, channel)):
            if channel not in effective:
                effective.append(channel)
    return effective
