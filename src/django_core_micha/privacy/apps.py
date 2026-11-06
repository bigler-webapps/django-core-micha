from django.apps import AppConfig
from django.core.checks import Error, Warning, register


class PrivacyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_core_micha.privacy"
    label = "dcm_privacy"

    def ready(self):
        from . import audit_config  # noqa: F401

        @register()
        def privacy_manifest_check(app_configs, **kwargs):
            from django.conf import settings
            from .manifest import validate_manifest_setting

            if not hasattr(settings, "DCM_PRIVACY"):
                return [Warning("DCM_PRIVACY is not configured.", id="dcm_privacy.W001")]
            _, errors = validate_manifest_setting(getattr(settings, "DCM_PRIVACY", None))
            return [
                Error(message, id=f"dcm_privacy.E{index:03d}")
                for index, message in enumerate(errors, start=1)
            ]
