"""In-repo per-language notification text templates.

There is no gettext catalogue anywhere in this repo (no ``.po``/``.mo`` files, no compile
step wired into the build) -- this mirrors the working ``emails/email_texts.py``
per-language-dict pattern instead of introducing gettext infrastructure that nothing else
uses. A consuming app registers its own keys the same way it registers its own
``NotificationType`` (see ``messaging.notification_texts.register_messaging_notification_texts``).
"""
import logging

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("de", "en", "fr")

_REGISTRY: dict[str, dict[str, str]] = {}


def register_notification_text(key: str, translations: dict[str, str]) -> None:
    """Register (or overwrite) the per-language templates for ``key``."""

    _REGISTRY[key] = dict(translations)


def resolve_notification_text(key: str, language: str) -> str | None:
    """Return ``key``'s template for ``language`` (English as the in-registry fallback),
    or ``None`` if ``key`` was never registered -- the caller decides what happens then."""

    translations = _REGISTRY.get(key)
    if translations is None:
        return None
    return translations.get(language, translations.get("en"))
