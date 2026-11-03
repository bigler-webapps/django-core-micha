"""Shared client IP resolution for rate limits and audit events."""
from __future__ import annotations

import ipaddress


def resolve_client_ip(request) -> str | None:
    """Return the normalized Cloudflare client IP or validated peer address.

    Forwarding headers other than Cloudflare's are deliberately ignored.
    """
    if request is None:
        return None
    meta = getattr(request, "META", {})
    for raw in (meta.get("HTTP_CF_CONNECTING_IP"), meta.get("REMOTE_ADDR")):
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            return str(ipaddress.ip_address(raw.strip()))
        except ValueError:
            continue
    return None


class ClientAddressThrottleMixin:
    """Use the shared client address as a DRF throttle identity."""

    def get_ident(self, request):
        return resolve_client_ip(request) or "unknown"
