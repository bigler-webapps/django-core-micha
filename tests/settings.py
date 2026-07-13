SECRET_KEY = "test-secret-not-for-production"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_core_micha.auditlog",
    "django_core_micha.notifications",
    "django_core_micha.onboarding",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "tests.testapp",
]

MIGRATION_MODULES = {
    "testapp": None,
    "account": None,
    "mfa": None,
    "socialaccount": None,
}

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
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# Override for test environment — no Redis available in CI
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
