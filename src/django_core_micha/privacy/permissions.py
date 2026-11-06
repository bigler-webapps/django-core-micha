from rest_framework.permissions import BasePermission

from .guard import has_grant


def HasPrivacyGrant(purpose_id, service_id=None):
    """Return a DRF permission class for one configured optional purpose (and, optionally, one of its services)."""
    class PurposePrivacyGrantPermission(BasePermission):
        message = "privacy_grant_required"
        code = "privacy_grant_required"

        def has_permission(self, request, view):
            return has_grant(request, purpose_id, service_id)

    safe_id = "".join(character if character.isalnum() else "_" for character in purpose_id)
    PurposePrivacyGrantPermission.__name__ = f"HasPrivacyGrant_{safe_id}"
    PurposePrivacyGrantPermission.__qualname__ = PurposePrivacyGrantPermission.__name__
    return PurposePrivacyGrantPermission
