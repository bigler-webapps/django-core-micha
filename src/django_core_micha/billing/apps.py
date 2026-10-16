from django.apps import AppConfig
from django.core.checks import register, Error
class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_core_micha.billing"
    label = "dcm_billing"
    def ready(self):
        from . import audit_config
        @register()
        def billing_config_check(app_configs, **kwargs):
            from .config import load_billing_config
            try: load_billing_config()
            except Exception as exc: return [Error(str(exc), id="dcm_billing.E001")]
            return []
