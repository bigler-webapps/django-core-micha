from django.conf import settings
from django.contrib.auth.backends import ModelBackend

from django_core_micha.auth.roles import (
    ROLE_LEVEL_0,
    ROLE_LEVEL_2,
    ROLE_LEVEL_3,
    get_role_code_for_user,
    get_default_role_code,
    get_role_definitions,
    get_role_level_for_code,
    get_role_level_for_user,
)


def _has_min_level(user, setting_name: str, default_level: int) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True

    min_level = getattr(settings, setting_name, default_level)
    user_level = get_role_level_for_user(user)
    return user_level >= int(min_level)


def _get_configured_roles(setting_name: str) -> set[str]:
    raw = getattr(settings, setting_name, ()) or ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(value).strip() for value in raw if str(value).strip()}


def _has_role_from_setting(user, setting_name: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True

    role_code = get_role_code_for_user(user)
    return role_code in _get_configured_roles(setting_name)


def _default_auth_policy_write_level() -> int:
    definitions = get_role_definitions() or {}
    levels = []
    for info in definitions.values():
        try:
            levels.append(int((info or {}).get("level", ROLE_LEVEL_0)))
        except (TypeError, ValueError, AttributeError):
            continue
    default_code = get_default_role_code()
    if default_code in definitions:
        try:
            default_level = get_role_level_for_code(default_code)
        except (TypeError, ValueError, AttributeError):
            default_level = max(levels, default=ROLE_LEVEL_0)
    elif len(set(levels)) > 1:
        # A genuine hierarchy with an unknown default code: a role-less user resolves to level 0
        # (get_role_level_for_user), so every defined level counts as above it.
        default_level = ROLE_LEVEL_0
    else:
        # A flat setup with an unknown default code: assume the highest defined level, so the
        # misconfiguration can never make one role level look like a hierarchy.
        default_level = max(levels, default=ROLE_LEVEL_0)
    # S249: a real hierarchy keeps its highest level as the threshold. A flat setup (nothing above the
    # level a role-less user has) must not make every default-role user a full auth admin: the
    # threshold then lies above that level and at least at ROLE_LEVEL_3.
    if any(level > default_level for level in levels):
        return max(levels)
    return max(ROLE_LEVEL_3, default_level + 1)


def has_full_auth_admin_rights(user) -> bool:
    return _has_min_level(
        user,
        "AUTH_POLICY_WRITE_MIN_ROLE_LEVEL",
        _default_auth_policy_write_level(),
    )


def has_invite_admin_rights(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    if has_full_auth_admin_rights(user):
        return True
    if _has_role_from_setting(user, "INVITE_ADMIN_ROLES"):
        return True
    return _has_min_level(user, "INVITE_MIN_ROLE_LEVEL", ROLE_LEVEL_2)


def has_access_code_admin_rights(user) -> bool:
    return has_invite_admin_rights(user)


def can_manage_support_agents(user) -> bool:
    return _has_min_level(user, "SUPPORT_ASSIGN_ROLE_LEVEL", ROLE_LEVEL_3)


def can_assign_support_contact(user) -> bool:
    return can_manage_support_agents(user)


def is_subject_to_admin_auth_policy(user) -> bool:
    return has_full_auth_admin_rights(user)


def can_user_authenticate(user) -> bool:
    if user is None:
        return False
    return ModelBackend().user_can_authenticate(user)
