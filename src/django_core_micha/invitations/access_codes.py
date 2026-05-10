# django_core_micha/invitations/access_codes.py
from django.conf import settings
from rest_framework.exceptions import ValidationError

from .models import AccessCode


def validate_access_code_or_error(code: str, *, consume: bool = False) -> AccessCode:
    """
    Prüft einen Access-Code und wirft bei Problemen eine DRF ValidationError.

    - code: übermittelter Code (String)
    - consume: wenn True, wird der Code danach deaktiviert (Single-Use)
    """
    if not code:
        raise ValidationError({"code": "Auth.ACCESS_CODE_REQUIRED"})

    try:
        ac = AccessCode.objects.get(code=code, is_active=True)
    except AccessCode.DoesNotExist:
        raise ValidationError({"code": "Auth.ACCESS_CODE_INVALID_OR_INACTIVE"})

    if consume:
        ac.is_active = False
        ac.save(update_fields=["is_active"])

    return ac

# django_core_micha/invitations/access_codes.py (oder in auth.permissions)




def is_invite_admin(user) -> bool:
    """
    True, wenn der User Einladungen / Access-Codes verwalten darf.
    Logik:
      - superuser: immer True
      - sonst: user.profile.role in INVITE_ADMIN_ROLES
    """
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True

    profile = getattr(user, "profile", None)
    allowed_roles = getattr(settings, "INVITE_ADMIN_ROLES", ("admin", "supervisor"))
    return bool(profile and getattr(profile, "role", None) in allowed_roles)
