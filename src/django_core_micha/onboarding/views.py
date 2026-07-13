from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django_core_micha.auth.roles import (
    ROLE_LEVEL_2,
    get_role_code_for_user,
    get_role_level_for_code,
)

from .models import OnboardingStepConfig, get_registered_step_keys, get_step_config_map


def _serialize_config(config):
    return {"key": config.key, "enabled": config.enabled, "order": config.order}


def _is_onboarding_admin(user) -> bool:
    if user.is_superuser:
        return True
    level = get_role_level_for_code(get_role_code_for_user(user))
    return level >= ROLE_LEVEL_2


class OnboardingStepConfigView(APIView):
    """Authenticated users may read config; elevated roles may bulk update it."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        keys = get_registered_step_keys()
        get_step_config_map()
        configs = OnboardingStepConfig.objects.filter(key__in=keys)
        return Response([_serialize_config(config) for config in configs])

    def patch(self, request):
        if not _is_onboarding_admin(request.user):
            raise PermissionDenied(_("Admin access required."))

        payload = request.data if isinstance(request.data, list) else [request.data]
        if not isinstance(request.data, (dict, list)):
            return Response(
                {"detail": _("Payload must be a list or dict of {key, enabled}.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        known_keys = set(get_registered_step_keys())
        errors = []
        updated = []
        for item in payload:
            if not isinstance(item, dict):
                errors.append("Every item must be an object.")
                continue
            key = (item.get("key") or "").strip()
            if key not in known_keys:
                errors.append(f"Unknown key: {key!r}")
                continue
            if "enabled" not in item:
                errors.append(f"Missing 'enabled' for key {key!r}")
                continue
            if not isinstance(item["enabled"], bool):
                errors.append(f"Enabled must be a boolean for key {key!r}")
                continue
            config, created = OnboardingStepConfig.objects.get_or_create(key=key)
            config.enabled = item["enabled"]
            config.save(update_fields=["enabled"])
            updated.append(_serialize_config(config))

        if errors:
            return Response({"errors": errors, "updated": updated}, status=status.HTTP_400_BAD_REQUEST)
        return Response(updated)

    def put(self, request):
        return self.patch(request)
