"""Custom DRF throttle classes for auth endpoints.

`PerEmailScopedRateThrottle` keys the throttle cache by the lowercased, hashed
target email instead of IP/user. This complements the standard per-IP throttles
on email-sending endpoints (register_request, password_reset, recovery_login)
and addresses the dominant abuse vector — distributed requests against a single
target — that IP-based throttling cannot cover.
"""
from __future__ import annotations

import hashlib

from rest_framework.throttling import ScopedRateThrottle


class PerEmailScopedRateThrottle(ScopedRateThrottle):
    """Scoped throttle keyed by the lowercased, SHA-1-hashed target email.

    Falls back to ``None`` (no throttling for this class) when the request body
    contains no email — the companion per-IP throttle still applies in that
    case.
    """

    def get_cache_key(self, request, view):
        # Resolve the scope the same way ScopedRateThrottle does.
        if not self.scope:
            return None

        email = self._extract_email(request)
        if not email:
            return None

        digest = hashlib.sha1(email.encode("utf-8")).hexdigest()
        return self.cache_format % {
            "scope": self.scope,
            "ident": digest,
        }

    @staticmethod
    def _extract_email(request) -> str | None:
        # Body only — query_params are not validated by the serializer and
        # would allow an attacker to seed a different cache bucket while the
        # body email is the real target.
        data = getattr(request, "data", None)
        if not data:
            return None
        try:
            value = data.get("email")
        except AttributeError:
            return None
        if value and isinstance(value, str):
            return value.strip().lower()
        return None
