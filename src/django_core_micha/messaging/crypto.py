"""Per-application Fernet keyring registration and encryption primitives.

`sync-secrets` consumers must provide one distinct ordered ring per app::

    MESSAGING_KEYRINGS = {
        "my_app": ["new-primary-fernet-key", "old-fernet-key"],
    }

Rotation is deploy-new-primary-with-old-keys, resumably re-encrypt stored values,
verify, then remove retired keys.  Rings are deliberately never inherited or
shared: registration fails before an app can use messaging.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


_registered_apps: set[str] = set()


def _configured_rings() -> dict[str, Any]:
    rings = getattr(settings, "MESSAGING_KEYRINGS", None)
    if not isinstance(rings, dict):
        raise ImproperlyConfigured("MESSAGING_KEYRINGS must be a mapping of app keys to Fernet rings.")
    return rings


def _normalise_ring(app_key: str) -> tuple[str, ...]:
    if not isinstance(app_key, str) or not app_key.strip():
        raise ImproperlyConfigured("Messaging app keys must be non-empty strings.")
    value = _configured_rings().get(app_key)
    if not isinstance(value, (list, tuple)) or not value:
        raise ImproperlyConfigured(f"MESSAGING_KEYRINGS[{app_key!r}] must be a non-empty ordered Fernet ring.")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise ImproperlyConfigured(f"MESSAGING_KEYRINGS[{app_key!r}] contains an empty or malformed Fernet key.")
    ring = tuple(value)
    if len(set(ring)) != len(ring):
        raise ImproperlyConfigured(f"MESSAGING_KEYRINGS[{app_key!r}] repeats a Fernet key.")
    try:
        for key in ring:
            Fernet(key.encode("ascii"))
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ImproperlyConfigured(f"MESSAGING_KEYRINGS[{app_key!r}] contains an invalid Fernet key.") from exc
    for other_key, other_ring in _configured_rings().items():
        if other_key != app_key and isinstance(other_ring, (list, tuple)) and set(ring).intersection(other_ring):
            raise ImproperlyConfigured(
                f"MESSAGING_KEYRINGS[{app_key!r}] shares a Fernet key with {other_key!r}; app rings must be distinct."
            )
    return ring


@lru_cache(maxsize=None)
def _build_multi_fernet(app_key: str, ring: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(key.encode("ascii")) for key in ring])


def register_messaging_app(app_key: str) -> None:
    """Validate and register an app's keyring; fail closed on every bad ring."""

    ring = _normalise_ring(app_key)
    _build_multi_fernet(app_key, ring)
    _registered_apps.add(app_key)


def unregister_messaging_app(app_key: str) -> None:
    """Test-only registry cleanup; consuming applications should not call this."""

    _registered_apps.discard(app_key)


def get_multi_fernet(app_key: str) -> MultiFernet:
    if app_key not in _registered_apps:
        raise ImproperlyConfigured(f"Messaging app {app_key!r} is not registered with a validated keyring.")
    return _build_multi_fernet(app_key, _normalise_ring(app_key))


def encrypt_text(*, app_key: str, value: str | None) -> str | None:
    if value is None:
        return None
    return get_multi_fernet(app_key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(*, app_key: str, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return get_multi_fernet(app_key).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ValueError("Messaging ciphertext could not be decrypted with the registered app keyring.") from exc


def encrypt_bytes(*, app_key: str, value: bytes) -> bytes:
    return get_multi_fernet(app_key).encrypt(value)


def decrypt_bytes(*, app_key: str, value: bytes) -> bytes:
    try:
        return get_multi_fernet(app_key).decrypt(value)
    except InvalidToken as exc:
        raise ValueError("Messaging ciphertext could not be decrypted with the registered app keyring.") from exc
