from django.apps import AppConfig
from django.core.checks import Error, Warning, register


class TermsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_core_micha.terms"
    label = "dcm_terms"

    def ready(self):
        from . import audit_config  # noqa: F401

        @register()
        def terms_document_check(app_configs, **kwargs):
            from django.conf import settings
            from .services import validate_document_setting
            if not hasattr(settings, "DCM_TERMS_DOCUMENT"):
                return [Warning("DCM_TERMS_DOCUMENT is not configured.", id="dcm_terms.W001")]
            _, errors = validate_document_setting(getattr(settings, "DCM_TERMS_DOCUMENT", None))
            return [Error(message, id="dcm_terms.E001") for message in errors]
