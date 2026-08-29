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

        # DCM-PERF-1: connect the auth-policy cache invalidation receiver at
        # startup, in every process — not just processes that happen to have
        # already called get_or_create_auth_policy() once. Without this, a
        # process that only ever WRITES the policy row (a management command,
        # a migration) would never register the receiver and its writes
        # would go unnoticed by every other process's cache until TTL expiry.
        from django_core_micha.auth.policy import (
            _connect_auth_policy_cache_signals,
            get_auth_policy_model,
        )
        policy_model = get_auth_policy_model()
        if policy_model is not None:
            _connect_auth_policy_cache_signals(policy_model)