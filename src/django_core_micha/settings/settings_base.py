# django_core_micha/settings_base.py

from corsheaders.defaults import default_headers
from django.conf import settings
import environ
import os
import logging

logger = logging.getLogger("backend")

# -------------------------------------------------------------------
# Environment
# -------------------------------------------------------------------

env = environ.Env(
    DEBUG=(bool, False),
    EMAIL_PORT=(int, 587),
    EMAIL_USE_TLS=(bool, True),
)

DEBUG = env("DEBUG", default=False)

# 1) Environment type: local / staging / production / edge / ...
ENV_TYPE = env("ENV_TYPE", default="production").lower()

SECRET_KEY = env("DJANGO_SECRET_KEY", default="local-dev-secret-key")

# 2) Origin server id
ORIGIN_SERVER_ID = os.environ.get("ORIGIN_SERVER_ID")

if not ORIGIN_SERVER_ID:
    if ENV_TYPE == "production":
        ORIGIN_SERVER_ID = "MASTER-UNKNOWN"
    elif ENV_TYPE == "staging":
        ORIGIN_SERVER_ID = "STAGING-UNKNOWN"
    else:
        ORIGIN_SERVER_ID = "DEV-LOCAL"

# 3) Bequeme Flags
IS_PRODUCTION = ENV_TYPE == "production"
IS_STAGING = ENV_TYPE == "staging"
IS_LOCAL = ENV_TYPE == "local"
IS_EDGE = ENV_TYPE == "edge" 

IS_MASTER = IS_PRODUCTION and ORIGIN_SERVER_ID.upper().startswith("MASTER")

if not IS_LOCAL and SECRET_KEY.strip() in ("", "local-dev-secret-key"):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set in non-local environments."
    )


logger.info(f"Starting with ENV_TYPE={ENV_TYPE}, ORIGIN_SERVER_ID={ORIGIN_SERVER_ID}, IS_MASTER={IS_MASTER}, IS_EDGE={IS_EDGE}, DEBUG={DEBUG}")

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")


# -------------------------------------------------------------------
# Hosts & Networking
# -------------------------------------------------------------------

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_URLS", default=[])
# CORS_ALLOWED_URLS optional; fällt auf CSRF-Trust-Liste zurück (rückwärtskompatibel).
# Getrennte Env-Var erlaubt, CORS bewusst weiter zu öffnen ohne CSRF-Trust mitzuerweitern.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_URLS", default=CSRF_TRUSTED_ORIGINS)
PUBLIC_ORIGIN = env("PUBLIC_ORIGIN", default="http://localhost:3000")

if not IS_LOCAL:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_REFERRER_POLICY = "same-origin"

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not IS_LOCAL
CSRF_COOKIE_SECURE = not IS_LOCAL

# -------------------------------------------------------------------
# Applications
# -------------------------------------------------------------------

CORE_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third-party
    "corsheaders",
    "rest_framework",
    "channels",
    "allauth",
    "allauth.mfa",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.microsoft",
    # Core app(s)
    "django_core_micha.invitations",
    "django_core_micha.auth",
    "django_core_micha.auditlog",
]

INSTALLED_APPS = CORE_APPS.copy()

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # S198: set audit actor + request_id ContextVars for every request.
    # Must come after AuthenticationMiddleware so request.user is populated.
    "django_core_micha.auditlog.middleware.AuditlogActorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # S19: Enforce MFA enrollment for Django admin in non-local envs.
    # Opt-out via `ADMIN_MFA_REQUIRED = False` in the project's settings.
    # Placed AFTER AccountMiddleware so allauth-session state is available.
    "django_core_micha.auth.admin_mfa_middleware.AdminMfaRequiredMiddleware",
]

# S19: Set to False to disable admin-MFA enforcement (not recommended in prod).
ADMIN_MFA_REQUIRED = True

# S198: Retention window for AuditEvent rows (days). Override per-app via ENV.
AUDITLOG_RETENTION_DAYS = int(os.getenv("AUDITLOG_RETENTION_DAYS", "730"))

# ROOT_URLCONF / WSGI_APPLICATION / ASGI_APPLICATION / SITE_ID
# bleiben im Projekt (backend/settings.py), nicht im Core.


# -------------------------------------------------------------------
# Database
# -------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="db_build_dummy"),
        "USER": env("DB_USER", default="user_build_dummy"),
        "PASSWORD": env("DB_PASSWORD", default="pass_build_dummy"),
        "HOST": env("DB_HOST", default="db"),
        "PORT": env("DB_PORT", default="5432"),
    }
}

# -------------------------------------------------------------------
# Channels / Redis
# -------------------------------------------------------------------

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [(env("REDIS_HOST", default="redis"), 6379)],
        },
    },
}

PROJECT_NAME = env("PROJECT_NAME", default="Project")


# -------------------------------------------------------------------
# Email
# -------------------------------------------------------------------

EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if IS_LOCAL
    else "django.core.mail.backends.smtp.EmailBackend"
)

EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env("EMAIL_PORT")
EMAIL_USE_TLS = env("EMAIL_USE_TLS")
EMAIL_HOST_USER = env("EMAIL_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_PASSWORD", default="")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

if not IS_LOCAL and not (EMAIL_HOST and EMAIL_HOST_PASSWORD):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "EMAIL_HOST and EMAIL_PASSWORD must be set in non-local environments."
    )

# -------------------------------------------------------------------
# Templates (Projekt setzt DIRS / BASE_DIR)
# -------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],  # wird im Projekt gesetzt
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# -------------------------------------------------------------------
# Static / Media
# -------------------------------------------------------------------

STATIC_URL = "/static/"
MEDIA_URL = "/media/"

if IS_LOCAL:
    # Local dev should serve freshly built assets without requiring a backend restart.
    STORAGES = {
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
        },
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
    }
    WHITENOISE_AUTOREFRESH = True
    WHITENOISE_USE_FINDERS = True
else:
    STORAGES = {
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
    }

# STATIC_ROOT / STATICFILES_DIRS / MEDIA_ROOT hängen von BASE_DIR ab
# und werden im Projekt gesetzt.


# -------------------------------------------------------------------
# Auth / Allauth / REST Framework
# -------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Plattform-Default seit 2.12.0 (S70 Phase F): jede ViewSet/APIView ohne
    # explizites `permission_classes`-Attribute UND ohne `get_permissions()`-Override
    # bekommt deny-by-default. Public-Endpoints muessen `permission_classes =
    # [AllowAny]` (oder eine Subklasse) explizit setzen. Siehe
    # webapp-management/PLATFORM_PERMISSION_AUDIT.md fuer Audit-Trail je Konsument.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "django_core_micha.auth.exception_handler.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/day",
        "user": "10000/day",
        "sync_client": "1000/minute",
        "password_reset": "50/hour",
        "invite_anon": "30/hour",
        "invite_admin": "500/hour",
        "access_code_validate": "100/hour",
        "mfa_support_help": "5/hour",
        "recovery_login": "30/hour",
        # Per-target-email throttles (PerEmailScopedRateThrottle). Address
        # distributed attacks that bypass per-IP limits via proxy rotation.
        "email_register_request": "4/hour",
        "email_password_reset": "4/hour",
        "email_recovery_login": "4/hour",
        # S53: per-email cap on mfa_support_help. The per-IP limit
        # (`mfa_support_help: 5/hour` above) lets a proxy-rotating attacker
        # flood RecoveryRequest rows for a known target email.
        "email_mfa_support_help": "4/hour",
        # S52: per-access-code throttle for register_request and the
        # standalone /access-codes/validate endpoint
        # (PerAccessCodeScopedRateThrottle). Closes the gap where
        # `consume=False` validation lets one attacker probe one code many
        # times by rotating the target email.
        #
        # NB: register_request and /access-codes/validate SHARE this bucket
        # per-code by design — an attacker who exhausts one endpoint cannot
        # fall through to the other for fresh quota. The trade-off is that
        # a legitimate user who retries the same code across both flows
        # (typo on signup → re-validate → retry) consumes the bucket
        # combined. 10/hour leaves enough headroom for that.
        "access_code_probe": "10/hour",
    },
}

SECURITY_LEVELS = ("anon", "recovery", "basic", "strong")

# Per App konfigurierbar (in Projektsettings überschreibbar)
SECURITY_DEFAULT_LEVEL = env("SECURITY_DEFAULT_LEVEL", default="basic")
RECOVERY_REQUEST_TTL_MINUTES = env("RECOVERY_REQUEST_TTL_MINUTES", default=30)



ACCOUNT_ADAPTER = "django_core_micha.auth.adapters.CoreAccountAdapter"
MFA_ADAPTER = "django_core_micha.auth.adapters.CoreMFAAdapter"
SOCIALACCOUNT_ADAPTER = "django_core_micha.auth.adapters.InvitationOnlySocialAdapter"

ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {'email'}

ACCOUNT_SIGNUP_FIELDS = ['email*']
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "optional"

# S212 — Brute-Force + Credential-Stuffing-Mitigation via allauth built-in rate limiter.
# Backed by Django cache (Redis in production). Disabled locally so dev/test flows are
# not disrupted by a Redis dependency and test isolation is trivial.
if not IS_LOCAL:
    ACCOUNT_RATE_LIMITS = {
        "login_failed":   "5/5m/ip,10/h/user",
        "login":          "30/m/ip",
        "signup":         "10/h/ip",
        "password_reset": "5/h/ip,3/h/user",
        "reauthenticate": "10/m/user",
        "confirm_email":  "3/h/user",
        "manage_email":   "10/h/user",
    }

LOGIN_REDIRECT_URL = "/"
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

ACCOUNT_SIGNUP_FIELDS = [
    "email*",
]

MFA_WEBAUTHN_RP_NAME = env("MFA_WEBAUTHN_RP_NAME", default="Project")
MFA_SUPPORTED_TYPES = ["webauthn", "totp", "recovery_codes"]  # optional, falls du später mehr MFA willst

MFA_PASSKEY_LOGIN_ENABLED = True

SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_LOGIN_ON_GET = False  # S11: require POST + CSRF for social-login initiation


SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_CLIENT_ID", default=""),
            "secret": env("GOOGLE_SECRET", default=""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "EMAIL_AUTHENTICATION": True,
    },
    "microsoft": {
        "APP": {
            "client_id": env("MICROSOFT_CLIENT_ID", default=""),
            "secret": env("MICROSOFT_SECRET", default=""),
            "key": "",
        },
        "SCOPE": ["User.Read"],
        "AUTH_PARAMS": {
            "prompt": "select_account",
        },
    },
}

# App-level auth capability switches.
# Projects can override selected keys in their own settings.py.
AUTH_METHODS = {
    "password_login": True,
    "password_reset": True,
    "password_change": True,
    "social_login": True,
    "social_providers": ["google", "microsoft"],
    "passkey_login": True,
    "passkeys_manage": True,
    "mfa_totp": True,
    "mfa_recovery_codes": True,
}



HEADLESS_ONLY = False
HEADLESS_CLIENTS = ["browser"]
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{PUBLIC_ORIGIN}/email-verify/{{key}}",
    "account_reset_password": f"{PUBLIC_ORIGIN}/reset-request-password",
    "account_reset_password_from_key": f"{PUBLIC_ORIGIN}/password-reset/{{key}}",
    "account_signup": f"{PUBLIC_ORIGIN}/signup",
    "socialaccount_login_error": f"{PUBLIC_ORIGIN}/login?social=error",
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = list(default_headers) + ["X-Admin-Token", "X-CSRFToken"]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Zurich"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_pii": {
            "()": "django_core_micha.logging_filters.SensitiveDataFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["redact_pii"],
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO"},
        "backend": {  # fest, weil du immer 'backend' nutzt
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
        },
    },
}
