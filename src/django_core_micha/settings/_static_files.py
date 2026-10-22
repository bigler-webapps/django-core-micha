"""WhiteNoise immutable-file detection for Django and Vite static assets."""

import re
from urllib.parse import urlparse


_VITE_FILENAME = re.compile(r"^.+-([A-Za-z0-9_-]{8})\.[^.]+$")
_CAPITALIZED_WORDS = re.compile(r"(?:[A-Z]?[a-z]+)+\Z")


def _static_prefix(settings):
    """The prefix exactly as WhiteNoiseMiddleware.__init__ derives it."""
    from whitenoise.string_utils import ensure_leading_trailing_slash

    try:
        prefix = settings.WHITENOISE_STATIC_PREFIX
    except AttributeError:
        prefix = urlparse(settings.STATIC_URL or "").path
        if settings.FORCE_SCRIPT_NAME:
            script_name = settings.FORCE_SCRIPT_NAME.rstrip("/")
            if prefix.startswith(script_name):
                prefix = prefix[len(script_name):]
    return ensure_leading_trailing_slash(prefix)


def _django_manifest_immutable(path, url, settings):
    """Keep WhiteNoise's built-in Django-manifest hash check intact."""
    from whitenoise.middleware import WhiteNoiseMiddleware

    class _WhiteNoiseMethods:
        static_prefix = _static_prefix(settings)
        autorefresh = False
        get_name_without_hash = WhiteNoiseMiddleware.get_name_without_hash
        get_static_url = WhiteNoiseMiddleware.get_static_url

    return WhiteNoiseMiddleware.immutable_file_test(_WhiteNoiseMethods(), path, url)


def _is_vite_hashed_asset(url, settings):
    prefix = _static_prefix(settings)
    if not url.startswith(prefix):
        return False

    relative_name = url[len(prefix):]
    if not relative_name or "/" in relative_name:
        return False

    match = _VITE_FILENAME.fullmatch(relative_name)
    if not match:
        return False

    hash_part = match.group(1)
    if not (re.search(r"[A-Z]", hash_part) and re.search(r"[a-z]", hash_part)):
        return False

    return _CAPITALIZED_WORDS.fullmatch(hash_part) is None


def immutable_file_test(path, url):
    """Return whether WhiteNoise should give this file an immutable cache header."""
    from django.conf import settings

    try:
        if _django_manifest_immutable(path, url, settings):
            return True
    except (AttributeError, TypeError, ValueError):
        # If WhiteNoise cannot resolve a manifest name, fail closed for it.
        pass

    return _is_vite_hashed_asset(url, settings)
