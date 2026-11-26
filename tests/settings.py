import os

SECRET_KEY = "test-secret-not-for-production"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_core_micha.auth",
    "django_core_micha.auditlog",
    "django_core_micha.invitations",
    "django_core_micha.notifications",
    "django_core_micha.messaging",
    "django_core_micha.assistant",
    "django_core_micha.ai_policy",
    "django_core_micha.billing",
    "django_core_micha.receipts",
    "django_core_micha.terms",
    "django_core_micha.privacy",
    "django_core_micha.activity",
    "django_core_micha.test_management",
    "django_core_micha.onboarding",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "tests.testapp",
]

MIDDLEWARE = [
    "allauth.account.middleware.AccountMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

AUDITLOG_RETENTION_DAYS = 730

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DCM_TEST_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DCM_TEST_DB_PORT", "55432"),
        "NAME": os.environ.get("DCM_TEST_DB_NAME", "dcm_test"),
        "USER": os.environ.get("DCM_TEST_DB_USER", "dcm_test"),
        "PASSWORD": os.environ.get("DCM_TEST_DB_PASSWORD", "dcm_test"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# Deliberately distinct test rings: messaging registration rejects shared rings.
MESSAGING_KEYRINGS = {
    "test-app": ["m6QODq43eUafIhXbCCHyjPDONKy3Q3JUM9Y9E0kxPdk="],
    "other-app": ["Oir41I0GSn0fZpsjVLToz3WaBDtYJ4wJoYaKJpFfgQ0="],
}

# Override for test environment — no Redis available in CI
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
