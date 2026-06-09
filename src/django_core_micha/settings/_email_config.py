import logging
import warnings

_logger = logging.getLogger("backend")

_CONSOLE = "django.core.mail.backends.console.EmailBackend"
_SMTP = "django.core.mail.backends.smtp.EmailBackend"


def resolve_email_backend(provider, is_local, debug, host, password, resend_key, postmark_token):
    """
    Returns (backend_path: str, anymail_config: dict | None).

    Never raises — on missing credentials, emits a warning via both logger and
    warnings.warn (visible even before Django's logging is fully configured) and
    falls back to the console backend so the app always boots.
    """
    if not provider:
        if is_local or debug:
            return _CONSOLE, None
        if not (host and password):
            _warn(
                "No EMAIL_PROVIDER set and EMAIL_HOST/EMAIL_PASSWORD not configured "
                "in non-local environment — using console backend"
            )
            return _CONSOLE, None
        return _SMTP, None

    if provider == "console":
        return _CONSOLE, None

    if provider == "smtp":
        if not (host and password):
            _warn(
                "EMAIL_PROVIDER=smtp requires EMAIL_HOST and EMAIL_PASSWORD — "
                "falling back to console backend"
            )
            return _CONSOLE, None
        return _SMTP, None

    if provider == "resend":
        if not resend_key:
            _warn(
                "EMAIL_PROVIDER=resend requires RESEND_API_KEY — "
                "falling back to console backend"
            )
            return _CONSOLE, None
        return "anymail.backends.resend.EmailBackend", {"RESEND_API_KEY": resend_key}

    if provider == "postmark":
        if not postmark_token:
            _warn(
                "EMAIL_PROVIDER=postmark requires POSTMARK_SERVER_TOKEN — "
                "falling back to console backend"
            )
            return _CONSOLE, None
        return "anymail.backends.postmark.EmailBackend", {"POSTMARK_SERVER_TOKEN": postmark_token}

    _warn(f"Unknown EMAIL_PROVIDER={provider!r} — falling back to console backend")
    return _CONSOLE, None


def _warn(msg):
    _logger.warning(msg)
    warnings.warn(msg, UserWarning, stacklevel=3)
