from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime

SIGNUP_MODE_ADMIN_INVITE = "admin_invite"
SIGNUP_MODE_ACCESS_CODE = "self_signup_access_code"
SIGNUP_MODE_OPEN = "self_signup_open"
SIGNUP_MODE_EMAIL_DOMAIN = "self_signup_email_domain"
SIGNUP_MODE_QR = "self_signup_qr"

SELF_SIGNUP_MODES = (
    SIGNUP_MODE_ACCESS_CODE,
    SIGNUP_MODE_OPEN,
    SIGNUP_MODE_EMAIL_DOMAIN,
    SIGNUP_MODE_QR,
)

AUTH_FACTOR_SINGLE = 1
AUTH_FACTOR_TWO = 2

SIGNUP_TOKEN_SALT = "django_core_micha.auth.signup_context"
# Cryptographic max_age fallback when no policy is configured. 1 year is generous
# enough for staging/dev but bounded — apps with an AuthPolicy override this via
# `signup_qr_expiry_days`.
DEFAULT_SIGNUP_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 365
DEFAULT_SIGNUP_QR_EXPIRY_DAYS = 90


@dataclass(frozen=True)
class RegistrationPolicyState:
    allow_admin_invite: bool
    allow_self_signup_access_code: bool
    allow_self_signup_open: bool
    allow_self_signup_email_domain: bool
    allow_self_signup_qr: bool
    allowed_email_domains: list[str]
    required_auth_factor_count: int
    admin_required_auth_factor_count: int
    signup_qr_expiry_days: int
    require_email_verification: bool
    access_code_single_use: bool

    @property
    def signup_modes(self) -> list[str]:
        modes: list[str] = []
        if self.allow_self_signup_access_code:
            modes.append(SIGNUP_MODE_ACCESS_CODE)
        if self.allow_self_signup_open:
            modes.append(SIGNUP_MODE_OPEN)
        if self.allow_self_signup_email_domain:
            modes.append(SIGNUP_MODE_EMAIL_DOMAIN)
        if self.allow_self_signup_qr:
            modes.append(SIGNUP_MODE_QR)
        return modes

    @property
    def two_factor_required(self) -> bool:
        return int(self.required_auth_factor_count) >= AUTH_FACTOR_TWO

    @property
    def admin_two_factor_required(self) -> bool:
        return int(self.admin_required_auth_factor_count) >= AUTH_FACTOR_TWO


def _normalize_domains(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        domain = item.strip().lower().lstrip("@")
        if not domain or "@" in domain or domain in seen:
            continue
        seen.add(domain)
        normalized.append(domain)
    return normalized


def _default_required_factor_count() -> int:
    if getattr(settings, "SECURITY_ENFORCE_STRONG_AUTH", False) and str(
        getattr(settings, "SECURITY_DEFAULT_LEVEL", "basic")
    ).lower() == "strong":
        return AUTH_FACTOR_TWO
    return AUTH_FACTOR_SINGLE


def _default_signup_qr_expiry_days() -> int:
    try:
        configured = int(
            getattr(settings, "DEFAULT_SIGNUP_QR_EXPIRY_DAYS", DEFAULT_SIGNUP_QR_EXPIRY_DAYS)
        )
    except (TypeError, ValueError):
        configured = DEFAULT_SIGNUP_QR_EXPIRY_DAYS
    return max(1, configured)


def _normalize_signup_qr_expiry_days(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _default_signup_qr_expiry_days()
    return max(1, parsed)


def _normalize_factor_count(value: Any, fallback: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = AUTH_FACTOR_SINGLE if fallback is None else int(fallback)
    return AUTH_FACTOR_TWO if parsed >= AUTH_FACTOR_TWO else AUTH_FACTOR_SINGLE


def get_auth_policy_model():
    configured = getattr(settings, "AUTH_POLICY_MODEL", None)
    if not configured:
        return None

    if isinstance(configured, str):
        try:
            return apps.get_model(configured)
        except Exception:
            return None

    if hasattr(configured, "_meta"):
        return configured
    return None


def get_or_create_auth_policy():
    model = get_auth_policy_model()
    if model is None:
        return None
    try:
        obj, _created = model.objects.get_or_create(pk=1)
        return obj
    except Exception:
        return None


def get_policy_state(policy=None) -> RegistrationPolicyState:
    if policy is None:
        policy = get_or_create_auth_policy()

    if policy is None:
        access_code_enabled = bool(getattr(settings, "ACCESS_CODE_REGISTRATION_ENABLED", False))
        return RegistrationPolicyState(
            allow_admin_invite=True,
            allow_self_signup_access_code=access_code_enabled,
            allow_self_signup_open=False,
            allow_self_signup_email_domain=False,
            allow_self_signup_qr=False,
            allowed_email_domains=[],
            required_auth_factor_count=_default_required_factor_count(),
            admin_required_auth_factor_count=_default_required_factor_count(),
            signup_qr_expiry_days=_default_signup_qr_expiry_days(),
            require_email_verification=False,
            access_code_single_use=False,
        )

    required_auth_factor_count = _normalize_factor_count(
        getattr(policy, "required_auth_factor_count", AUTH_FACTOR_SINGLE)
    )
    return RegistrationPolicyState(
        allow_admin_invite=bool(getattr(policy, "allow_admin_invite", True)),
        allow_self_signup_access_code=bool(
            getattr(policy, "allow_self_signup_access_code", False)
        ),
        allow_self_signup_open=bool(getattr(policy, "allow_self_signup_open", False)),
        allow_self_signup_email_domain=bool(
            getattr(policy, "allow_self_signup_email_domain", False)
        ),
        allow_self_signup_qr=bool(getattr(policy, "allow_self_signup_qr", False)),
        allowed_email_domains=_normalize_domains(
            getattr(policy, "allowed_email_domains", [])
        ),
        required_auth_factor_count=required_auth_factor_count,
        admin_required_auth_factor_count=_normalize_factor_count(
            getattr(policy, "admin_required_auth_factor_count", required_auth_factor_count),
            fallback=required_auth_factor_count,
        ),
        signup_qr_expiry_days=_normalize_signup_qr_expiry_days(
            getattr(
                policy,
                "signup_qr_expiry_days",
                _default_signup_qr_expiry_days(),
            )
        ),
        require_email_verification=bool(
            getattr(policy, "require_email_verification", False)
        ),
        access_code_single_use=bool(
            getattr(policy, "access_code_single_use", False)
        ),
    )


def is_valid_signup_mode(mode: str) -> bool:
    return mode in SELF_SIGNUP_MODES


def mode_requires_access_code(mode: str) -> bool:
    return mode == SIGNUP_MODE_ACCESS_CODE


def mode_requires_email_domain(mode: str) -> bool:
    return mode == SIGNUP_MODE_EMAIL_DOMAIN


def mode_requires_qr_token(mode: str) -> bool:
    return mode == SIGNUP_MODE_QR


def is_allowed_email_domain(email: str, allowed_domains: list[str]) -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return domain in set(_normalize_domains(allowed_domains))


def build_public_auth_config(policy=None) -> dict[str, Any]:
    state = get_policy_state(policy)
    return {
        "signup": bool(state.signup_modes),
        "signup_modes": list(state.signup_modes),
        "required_auth_factor_count": int(state.required_auth_factor_count),
        "two_factor_required": state.two_factor_required,
        "admin_required_auth_factor_count": int(state.admin_required_auth_factor_count),
        "admin_two_factor_required": state.admin_two_factor_required,
        "qr_signup_enabled": bool(state.allow_self_signup_qr),
        "email_domain_hint": ", ".join(state.allowed_email_domains),
        "signup_qr_expiry_days": int(state.signup_qr_expiry_days),
        "email_verification_required": bool(state.require_email_verification),
    }


def serialize_policy(policy=None) -> dict[str, Any]:
    state = get_policy_state(policy)
    return {
        "allow_admin_invite": state.allow_admin_invite,
        "allow_self_signup_access_code": state.allow_self_signup_access_code,
        "allow_self_signup_open": state.allow_self_signup_open,
        "allow_self_signup_email_domain": state.allow_self_signup_email_domain,
        "allow_self_signup_qr": state.allow_self_signup_qr,
        "allowed_email_domains": list(state.allowed_email_domains),
        "required_auth_factor_count": int(state.required_auth_factor_count),
        "admin_required_auth_factor_count": int(state.admin_required_auth_factor_count),
        "signup_qr_expiry_days": int(state.signup_qr_expiry_days),
        "require_email_verification": state.require_email_verification,
        "access_code_single_use": state.access_code_single_use,
    }


def create_signup_context_token(
    *,
    registration_context: dict[str, Any] | None = None,
    label: str = "",
    expires_minutes: int | None = None,
    policy=None,
) -> tuple[str, str]:
    if expires_minutes is None:
        expires_minutes = get_policy_state(policy).signup_qr_expiry_days * 24 * 60
    else:
        expires_minutes = max(1, int(expires_minutes))
    expires_at = timezone.now() + timezone.timedelta(minutes=expires_minutes)
    payload = {
        "mode": SIGNUP_MODE_QR,
        "schema_version": "1",
        "label": (label or "").strip(),
        "expires_at": expires_at.isoformat(),
        "registration_context": registration_context or {},
    }
    token = signing.dumps(payload, salt=SIGNUP_TOKEN_SALT)
    return token, expires_at.isoformat()


def decode_signup_context_token(token: str) -> dict[str, Any]:
    if not token:
        raise signing.BadSignature("Missing token")

    # Cryptographic max_age comes from the active policy's signup_qr_expiry_days
    # (admin-configurable per app). Falls back to DEFAULT_SIGNUP_TOKEN_MAX_AGE_SECONDS
    # when no AUTH_POLICY_MODEL is configured, when no policy row exists yet,
    # or when DB access fails. Read-only: must not create a policy row as a
    # side-effect of decoding a token.
    max_age = DEFAULT_SIGNUP_TOKEN_MAX_AGE_SECONDS
    model = get_auth_policy_model()
    if model is not None:
        try:
            policy_obj = model.objects.filter(pk=1).first()
            if policy_obj is not None:
                state = get_policy_state(policy_obj)
                max_age = max(int(state.signup_qr_expiry_days), 1) * 86400
        except Exception:
            # Keep the safe fallback on any DB error.
            pass

    payload = signing.loads(
        token,
        salt=SIGNUP_TOKEN_SALT,
        max_age=max_age,
    )
    expires_raw = payload.get("expires_at")
    expires_at = parse_datetime(expires_raw) if isinstance(expires_raw, str) else None
    if expires_at is None:
        raise signing.BadSignature("Missing expiry")
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
    if expires_at <= timezone.now():
        raise signing.SignatureExpired("Token expired")
    return payload
