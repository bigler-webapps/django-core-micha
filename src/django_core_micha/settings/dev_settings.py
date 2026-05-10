# src/django_core_micha/dev_settings_invitations.py

SECRET_KEY = "dev-only-secret"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # deine Lib-App:
    "django_core_micha.invitations",
    "django_core_micha.auth",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",  # wir brauchen nur ein DB-Schema für Migrations
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
