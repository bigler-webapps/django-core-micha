SECRET_KEY = "test-secret-not-for-production"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_core_micha.auditlog",
    "tests.testapp",
]

MIGRATION_MODULES = {
    "testapp": None,
}

AUDITLOG_RETENTION_DAYS = 730

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
