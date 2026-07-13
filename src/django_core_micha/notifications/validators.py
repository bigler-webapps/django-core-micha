"""Validation helpers for browser push subscription endpoints."""

from urllib.parse import urlsplit


ALLOWED_PUSH_SERVICE_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
WNS_HOST_SUFFIX = ".notify.windows.com"


def is_allowed_push_endpoint(url: str) -> bool:
    """Return whether *url* uses an HTTPS endpoint from a known push service."""

    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return False

    return (
        parsed.scheme == "https"
        and hostname is not None
        and (hostname in ALLOWED_PUSH_SERVICE_HOSTS or hostname.endswith(WNS_HOST_SUFFIX))
    )
