"""Custom DRF throttle classes for auth endpoints.

`PerEmailScopedRateThrottle` keys the throttle cache by the lowercased, hashed
target email instead of IP/user. This complements the standard per-IP throttles
on email-sending endpoints (register_request, password_reset, recovery_login)
and addresses the dominant abuse vector — distributed requests against a single
target — that IP-based throttling cannot cover.

`PerAccessCodeScopedRateThrottle` (S52) addresses the symmetric gap on access-
code validation: `validate_access_code_or_error(..., consume=False)` lets a
single attacker probe a single access code many times without consuming it,
and the per-email throttle can be evaded by rotating the target email. Keying
on the access-code hash closes that loop.
"""
from __future__ import annotations

import hashlib

from rest_framework.throttling import ScopedRateThrottle


class _BodyKeyedScopedRateThrottle(ScopedRateThrottle):
    """Shared base for per-body-field scoped throttles.

    Subclasses override `_extract_key()` to return the lowercased identifier
    that should bucket the cache key. When `_extract_key()` returns ``None``
    the throttle is a no-op for this class — the companion per-IP throttle
    still bites because DRF evaluates throttle classes independently.
    """

    BODY_FIELD: str = ""  # subclass override

    def get_cache_key(self, request, view):
        # Resolve the scope the same way ScopedRateThrottle does.
        if not self.scope:
            return None

        ident = self._extract_key(request)
        if not ident:
            return None

        digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()
        return self.cache_format % {
            "scope": self.scope,
            "ident": digest,
        }

    @classmethod
    def _extract_key(cls, request) -> str | None:
        # Body only — query_params are not validated by the serializer and
        # would allow an attacker to seed a different cache bucket while the
        # body field is the real target.
        if not cls.BODY_FIELD:
            return None
        data = getattr(request, "data", None)
        if not data:
            return None
        try:
            value = data.get(cls.BODY_FIELD)
        except AttributeError:
            return None
        if value and isinstance(value, str):
            return value.strip().lower()
        return None


class PerEmailScopedRateThrottle(_BodyKeyedScopedRateThrottle):
    """Scoped throttle keyed by the lowercased, SHA-256-hashed target email.

    Reads both `email` and the `mfa_support_help`-specific `identifier` body
    field — the latter is documented as an alias on that endpoint (see
    `BaseUserViewSet.mfa_support_help`, which reads
    ``request.data.get("email") or request.data.get("identifier")``).
    Throttling only `email` would let a caller bypass the per-email cap by
    posting `{"identifier": "victim@example.com"}` with no `email` key.

    Other email-throttled endpoints (`register_request`, `reset_request`,
    `recovery_login`) do not accept `identifier`, so consulting it there is
    dead-code-safe.
    """

    BODY_FIELD = "email"

    @classmethod
    def _extract_key(cls, request) -> str | None:
        data = getattr(request, "data", None)
        if not data:
            return None
        try:
            value = data.get("email") or data.get("identifier")
        except AttributeError:
            return None
        if value and isinstance(value, str):
            return value.strip().lower()
        return None


class PerAccessCodeScopedRateThrottle(_BodyKeyedScopedRateThrottle):
    """Scoped throttle keyed by the lowercased, SHA-256-hashed access code.

    S52: prevents per-email-throttle evasion via target-email rotation when
    probing access codes (validate-only path with `consume=False`). Applied
    in tandem with the per-email throttle on register_request and on the
    standalone /access-codes/validate endpoint.
    """

    BODY_FIELD = "access_code"

    @classmethod
    def _extract_key(cls, request) -> str | None:
        # The standalone AccessCodeViewSet.validate endpoint accepts the
        # access code under the body field `code`; register_request and the
        # registration flow use `access_code`. Probe both so the same
        # throttle class can be reused at both call sites without forcing
        # call-site-specific subclasses.
        data = getattr(request, "data", None)
        if not data:
            return None
        try:
            value = data.get("access_code") or data.get("code")
        except AttributeError:
            return None
        if value and isinstance(value, str):
            return value.strip().lower()
        return None
