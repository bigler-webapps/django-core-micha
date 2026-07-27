from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "django_core_micha.notifications"
    label = "django_core_micha_notifications"
    verbose_name = "Core Notifications"

    def ready(self):
        # Register the GenericFK-backed todo model without mixing it into the
        # canonical notification model module.
        from .todo import models  # noqa: F401
