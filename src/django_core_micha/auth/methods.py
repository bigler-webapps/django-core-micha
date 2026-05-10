from __future__ import annotations

from collections.abc import Mapping
from django.conf import settings
from .policy import build_public_auth_config


DEFAULT_AUTH_METHODS = {
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


def _to_bool(value, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _to_provider_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                provider = item.strip()
                if provider:
                    out.append(provider)
        return out
    return []


def _configured_social_providers() -> list[str]:
    providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {}) or {}
    if not isinstance(providers, Mapping):
        return []

    configured: list[str] = []
    for provider_id, cfg in providers.items():
        if not isinstance(provider_id, str):
            continue
        if not isinstance(cfg, Mapping):
            continue

        app_cfg = cfg.get("APP") or {}
        if not isinstance(app_cfg, Mapping):
            continue

        client_id = str(app_cfg.get("client_id") or "").strip()
        secret = str(app_cfg.get("secret") or "").strip()
        if client_id and secret:
            configured.append(provider_id)

    return configured


def get_auth_methods() -> dict:
    """
    Computes effective auth capabilities for the current project.
    Projects can override this by defining AUTH_METHODS in settings.py.
    """
    raw = getattr(settings, "AUTH_METHODS", {}) or {}
    if not isinstance(raw, Mapping):
        raw = {}

    supported_mfa = set(getattr(settings, "MFA_SUPPORTED_TYPES", []) or [])
    passkey_supported = (
        "webauthn" in supported_mfa
        and bool(getattr(settings, "MFA_PASSKEY_LOGIN_ENABLED", False))
    )

    configured_social = _configured_social_providers()
    requested_providers = _to_provider_list(
        raw.get("social_providers", DEFAULT_AUTH_METHODS["social_providers"])
    )
    if requested_providers:
        social_providers = [p for p in requested_providers if p in configured_social]
    else:
        social_providers = configured_social

    social_login_enabled = _to_bool(
        raw.get("social_login", DEFAULT_AUTH_METHODS["social_login"]),
        True,
    ) and bool(social_providers)

    methods = {
        "password_login": _to_bool(
            raw.get("password_login", DEFAULT_AUTH_METHODS["password_login"]),
            True,
        ),
        "password_reset": _to_bool(
            raw.get("password_reset", DEFAULT_AUTH_METHODS["password_reset"]),
            True,
        ),
        "password_change": _to_bool(
            raw.get("password_change", DEFAULT_AUTH_METHODS["password_change"]),
            True,
        ),
        "social_login": social_login_enabled,
        "social_providers": social_providers,
        "passkey_login": _to_bool(
            raw.get("passkey_login", DEFAULT_AUTH_METHODS["passkey_login"]),
            passkey_supported,
        )
        and passkey_supported,
        "passkeys_manage": _to_bool(
            raw.get("passkeys_manage", DEFAULT_AUTH_METHODS["passkeys_manage"]),
            passkey_supported,
        )
        and passkey_supported,
        "mfa_totp": _to_bool(
            raw.get("mfa_totp", DEFAULT_AUTH_METHODS["mfa_totp"]),
            "totp" in supported_mfa,
        )
        and "totp" in supported_mfa,
        "mfa_recovery_codes": _to_bool(
            raw.get("mfa_recovery_codes", DEFAULT_AUTH_METHODS["mfa_recovery_codes"]),
            "recovery_codes" in supported_mfa,
        )
        and "recovery_codes" in supported_mfa,
    }

    methods["mfa_enabled"] = bool(methods["mfa_totp"] or methods["mfa_recovery_codes"])
    methods.update(build_public_auth_config())
    methods["signup"] = bool(methods.get("signup_modes"))
    return methods
