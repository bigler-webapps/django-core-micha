from django.apps import AppConfig

class AssistantConfig(AppConfig):
    name = "django_core_micha.assistant"
    label = "dcm_assistant"
    verbose_name = "DCM Assistant Chat"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import audit_config  # noqa: F401
