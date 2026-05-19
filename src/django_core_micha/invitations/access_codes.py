# django_core_micha/invitations/access_codes.py
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import AccessCode, _compute_code_hmac


def validate_access_code_or_error(code: str, *, consume: bool = False) -> AccessCode:
    """
    Prüft einen Access-Code und wirft bei Problemen eine DRF ValidationError.

    - code: übermittelter Code (String)
    - consume: wenn True, wird der Code atomar deaktiviert (Single-Use, S18+R1)

    S18: Lookup geht über das HMAC-Feld (konstant-zeitlich, kein Plaintext-Compare
    in der DB-Engine). S13/R1: bei consume=True wird ein atomarer Compare-and-Swap
    benutzt, damit zwei simultane Confirms nicht beide den Code als gültig sehen.
    """
    if not code:
        raise ValidationError({"code": "Auth.ACCESS_CODE_REQUIRED"})

    code_hmac = _compute_code_hmac(code)

    # `.filter().first()` instead of `.get()` so a hypothetical HMAC collision
    # or duplicate empty-code edge case returns deterministic-None rather than
    # raising MultipleObjectsReturned. The plaintext-code field is unique, so
    # in practice this guards against degenerate inputs only.
    ac = AccessCode.objects.filter(code_hmac=code_hmac, is_active=True).first()
    if ac is None:
        raise ValidationError({"code": "Auth.ACCESS_CODE_INVALID_OR_INACTIVE"})

    if consume:
        rows = AccessCode.objects.filter(pk=ac.pk, is_active=True).update(
            is_active=False
        )
        if rows == 0:
            # Race: another confirm consumed the code between get and update.
            raise ValidationError({"code": "Auth.ACCESS_CODE_ALREADY_USED"})
        ac.is_active = False

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
