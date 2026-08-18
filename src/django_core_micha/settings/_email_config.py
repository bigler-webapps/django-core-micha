import logging
import warnings

_logger = logging.getLogger("backend")

_CONSOLE = "django.core.mail.backends.console.EmailBackend"


def resolve_email_backend(provider, is_local, debug, resend_key):
    """
    Returns (backend_path: str, anymail_config: dict | None).

    Never raises — on missing credentials, emits a warning via both logger and
    warnings.warn (visible even before Django's logging is fully configured) and
    falls back to the console backend so the app always boots. Only console
    and resend are supported providers.
    """
    if not provider:
        if is_local or debug:
            return _CONSOLE, None
        return _resend_or_fallback(resend_key)

    if provider == "console":
        return _CONSOLE, None

    if provider == "resend":
        return _resend_or_fallback(resend_key)

    _warn(f"Unknown EMAIL_PROVIDER={provider!r} — falling back to console backend")
    return _CONSOLE, None


def _resend_or_fallback(resend_key):
    if not resend_key:
        _warn(
            "EMAIL_PROVIDER=resend requires RESEND_API_KEY — "
            "falling back to console backend"
        )
        return _CONSOLE, None
    return "anymail.backends.resend.EmailBackend", {"RESEND_API_KEY": resend_key}


def build_mailers(backend):
    """Return the settings.MAILERS configuration for a resolved backend."""
    return {"default": {"BACKEND": backend}}


def _warn(msg):
    _logger.warning(msg)
    warnings.warn(msg, UserWarning, stacklevel=3)
