import importlib
import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AuditlogConfig(AppConfig):
    name = "django_core_micha.auditlog"
    label = "django_core_micha_auditlog"
    verbose_name = "Platform Auditlog"

    def ready(self):
        from django.apps import apps as django_apps

        for app_config in django_apps.get_app_configs():
            module_path = f"{app_config.name}.audit_config"
            try:
                importlib.import_module(module_path)
            except ModuleNotFoundError as exc:
                # Only silence "module not found" for the audit_config itself,
                # not for transitive imports within it (R1).
                if exc.name != module_path:
                    logger.exception("auditlog: import error in %s", module_path)
            except Exception:
                logger.exception("auditlog: failed to load %s", module_path)

        from .signals import connect_signals
        connect_signals()
