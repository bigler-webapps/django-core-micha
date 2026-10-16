from django.apps import AppConfig
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured


class ReceiptsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_core_micha.receipts"
    label = "dcm_receipts"

    def ready(self):
        from . import audit_config  # noqa: F401

        @register()
        def receipts_reader_check(app_configs, **kwargs):
            from django.conf import settings
            import re
            errors=[]
            if getattr(settings, "DCM_RECEIPTS_READER", "live") not in ("live", "fake"):
                errors.append(Error("DCM_RECEIPTS_READER must be 'live' or 'fake'.", id="dcm_receipts.E002"))
            if getattr(settings, "DCM_RECEIPTS_READER", "live") == "fake" and getattr(settings, "ENV_TYPE", "") not in {"local", "test"}:
                errors.append(Error("The fake receipt reader is allowed only in local and test environments.", id="dcm_receipts.E001"))
            fallback=getattr(settings,"DCM_RECEIPTS_FALLBACK_CURRENCY","CHF")
            valid=isinstance(fallback,str) and (fallback.lower()=="none" or re.fullmatch(r"[A-Za-z]{3}",fallback,flags=re.ASCII))
            if not valid:errors.append(Error("DCM_RECEIPTS_FALLBACK_CURRENCY must be a three-letter currency code or 'none'.",id="dcm_receipts.E003"))
            return errors

