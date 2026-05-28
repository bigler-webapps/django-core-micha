# src/django_core_micha/auth/apps.py
from django.apps import AppConfig

class CoreAuthConfig(AppConfig):
    name = 'django_core_micha.auth'
    label = "django_core_micha_auth"

    def ready(self):
        import django_core_micha.auth.signals
        from django.apps import apps as django_apps
        if django_apps.is_installed("allauth.mfa"):
            from django_core_micha.auth.signals import connect_mfa_signals
            connect_mfa_signals()