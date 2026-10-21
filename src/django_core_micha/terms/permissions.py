from rest_framework.permissions import BasePermission
from .services import has_accepted_current_terms


class HasAcceptedCurrentTerms(BasePermission):
    message = "terms_acceptance_required"
    code = "terms_acceptance_required"

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return has_accepted_current_terms(request.user)
