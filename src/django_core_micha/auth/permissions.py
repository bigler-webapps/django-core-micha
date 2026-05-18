from django.conf import settings
from rest_framework import permissions
from rest_framework.permissions import BasePermission
from django_core_micha.auth.access import (
    can_assign_support_contact as base_can_assign_support_contact,
    can_manage_support_agents as base_can_manage_support_agents,
    has_access_code_admin_rights as base_has_access_code_admin_rights,
    has_full_auth_admin_rights as base_has_full_auth_admin_rights,
    has_invite_admin_rights as base_has_invite_admin_rights,
    is_subject_to_admin_auth_policy,
)
from django_core_micha.auth.security import (
    get_security_level,
    is_level_sufficient,
    is_user_security_sufficient,
)
from django_core_micha.auth.roles import (
    get_role_level_for_user,
    ROLE_LEVEL_0,
)

def _admin_policy_satisfied(user, request=None) -> bool:
    if request is None:
        return True
    if not is_subject_to_admin_auth_policy(user):
        return True
    return is_user_security_sufficient(user, request=request)


def has_full_auth_admin_rights(user, request=None) -> bool:
    return base_has_full_auth_admin_rights(user) and _admin_policy_satisfied(
        user,
        request=request,
    )


# --- Public Logic Functions (kept for backward compatibility) ---

def has_invite_admin_rights(user, request=None) -> bool:
    return base_has_invite_admin_rights(user) and _admin_policy_satisfied(
        user,
        request=request,
    )


def has_access_code_admin_rights(user, request=None) -> bool:
    return base_has_access_code_admin_rights(user) and _admin_policy_satisfied(
        user,
        request=request,
    )


def can_view_users_admin(user, request=None) -> bool:
    return has_invite_admin_rights(user, request=request)


def can_view_invite_admin(user, request=None) -> bool:
    return has_invite_admin_rights(user, request=request)


def can_view_auth_policy(user, request=None) -> bool:
    return has_invite_admin_rights(user, request=request)


def can_write_auth_policy(user, request=None) -> bool:
    return has_full_auth_admin_rights(user, request=request)


def can_send_admin_invites(user, request=None) -> bool:
    return has_invite_admin_rights(user, request=request)


def can_manage_signup_qr(user, request=None) -> bool:
    return has_invite_admin_rights(user, request=request)


def can_manage_support_agents(user, request=None) -> bool:
    return base_can_manage_support_agents(user) and _admin_policy_satisfied(
        user,
        request=request,
    )


def can_assign_support_contact(user, request=None) -> bool:
    return base_can_assign_support_contact(user) and _admin_policy_satisfied(
        user,
        request=request,
    )


# --- DRF Permission Classes ---

class MinRoleLevelPermission(BasePermission):
    """
    Base class for view-specific overrides.
    Usage:
        class IsManager(MinRoleLevelPermission):
            min_level = ROLE_LEVEL_2
    """
    min_level = ROLE_LEVEL_0

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        # Superuser check is usually implicit, but good to be explicit
        if getattr(user, "is_superuser", False):
            return True
        return get_role_level_for_user(user) >= int(self.min_level)


class IsInviteAdminOrSuperuser(permissions.BasePermission):
    def has_permission(self, request, view):
        return has_invite_admin_rights(
            getattr(request, "user", None),
            request=request,
        )


class IsAccessCodeAdminOrSuperuser(permissions.BasePermission):
    def has_permission(self, request, view):
        return has_access_code_admin_rights(
            getattr(request, "user", None),
            request=request,
        )


class IsAssignedSupportOrAdmin(BasePermission):
    """
    Checks if the user has rights to manage support agents / recovery.
    """
    def has_object_permission(self, request, view, obj):
        return can_manage_support_agents(request.user, request=request)
    
    def has_permission(self, request, view):
        return can_manage_support_agents(request.user, request=request)


class IsSupportAgent(BasePermission):
    def has_permission(self, request, view):
        # No request context (e.g. unusual programmatic call): deny. The shared
        # `_admin_policy_satisfied` helper returns True for request=None as a
        # backward-compatible default for other call sites; here we override
        # that default because S17 explicitly gates a privileged role.
        if request is None:
            return False
        user = request.user
        if not user or not user.is_authenticated:
            return False
        # Support agents handle privileged recovery flows — they must satisfy
        # the admin-policy gate (typically: admin MFA active) regardless of role.
        # When the admin MFA policy is not active for the app, a support-agent
        # assignment is by definition redundant (users can self-reset), so the
        # gate effectively requires both the role AND the policy to be active.
        if not _admin_policy_satisfied(user, request=request):
            return False
        if getattr(user, "is_superuser", False):
            return True

        profile = getattr(user, "profile", None)
        return bool(getattr(profile, "is_support_agent", False))


class RequireStrongSecurity(BasePermission):
    """
    Authority on Security Levels.
    """
    required_level = "strong"

    def has_permission(self, request, view):
        # 1. Check current level
        current = get_security_level(request)
        
        # 2. Check sufficiency
        is_sufficient = is_level_sufficient(current, self.required_level)
        
        # 3. Check enforcement setting
        if getattr(settings, "SECURITY_ENFORCE_STRONG_AUTH", False):
            return is_sufficient
            
        # If enforcement is off, we allow access (but maybe logged elsewhere)
        return True
