from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

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
PENDING_REGISTRATION_SALT = "django_core_micha.auth.pending_registration"
# S13: Pending-registration tokens have a fixed 24h lifetime.
PENDING_REGISTRATION_MAX_AGE_SECONDS = 24 * 60 * 60
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


def get_signup_qr_token_model():
    """S30: optional per-app concrete model for DB-persistent QR tokens.

    Apps that don't configure ``SIGNUP_QR_TOKEN_MODEL`` keep the legacy stateless
    behaviour (signature-only verification, no use-counter).
    """
    configured = getattr(settings, "SIGNUP_QR_TOKEN_MODEL", None)
    if not configured:
        return None

    if isinstance(configured, str):
        try:
            return apps.get_model(configured)
        except Exception:
            logger.warning(
                "SIGNUP_QR_TOKEN_MODEL=%r could not be resolved; falling back "
                "to stateless QR-token behaviour. Check that the app is in "
                "INSTALLED_APPS and the model exists.",
                configured,
            )
            return None

    if hasattr(configured, "_meta"):
        return configured
    return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
        "access_code_single_use": state.access_code_single_use,
    }


def create_signup_context_token(
    *,
    registration_context: dict[str, Any] | None = None,
    label: str = "",
    expires_minutes: int | None = None,
    policy=None,
    max_redemptions: int = 1,
    created_by=None,
) -> tuple[str, str]:
    """Create a signed signup-context token and (if a DB model is configured)
    persist a tracking row with use-counter.

    S30: when ``settings.SIGNUP_QR_TOKEN_MODEL`` resolves to a concrete model,
    a row is created so that token usage can be capped via ``max_redemptions``.
    Without the setting, the call falls back to legacy stateless behaviour.
    """
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

    model = get_signup_qr_token_model()
    if model is not None:
        try:
            model.objects.create(
                token_hash=_hash_token(token),
                mode=SIGNUP_MODE_QR,
                registration_context=registration_context or {},
                expires_at=expires_at,
                max_redemptions=max(1, int(max_redemptions)),
                created_by=created_by,
            )
        except Exception:
            # If persistence fails (e.g. duplicate hash from clock collision),
            # keep the signed token usable but without DB-backed accounting.
            pass

    return token, expires_at.isoformat()


def decode_signup_context_token(token: str) -> dict[str, Any]:
    """Decode and validate a signup-context token.

    S30: when a DB model is configured, also enforces use-counter / max-redemption
    by checking the row state. Read-only: never mutates the row. Use
    ``consume_signup_context_token`` to atomically increment the counter at
    registration confirmation.
    """
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

    # S30: if DB-backed accounting is configured, enforce redemption cap.
    qr_model = get_signup_qr_token_model()
    if qr_model is not None:
        token_hash = _hash_token(token)
        try:
            row = qr_model.objects.filter(token_hash=token_hash).first()
        except Exception:
            row = None
        if row is not None:
            if row.expires_at <= timezone.now():
                raise signing.SignatureExpired("Token expired")
            if row.use_count >= row.max_redemptions:
                raise signing.SignatureExpired("Token exhausted")
        # If row is missing, the token may pre-date the DB model rollout.
        # Stay permissive: signature already passed.

    return payload


def consume_signup_context_token(token: str) -> None:
    """S30+R1: atomically increment use_count.

    Raises ``signing.SignatureExpired`` if the token is exhausted or expired.
    No-op when ``SIGNUP_QR_TOKEN_MODEL`` is not configured.
    """
    model = get_signup_qr_token_model()
    if model is None:
        return

    token_hash = _hash_token(token)
    now = timezone.now()
    with transaction.atomic():
        # Compare-and-swap: only increment if not exhausted and not expired.
        rows = model.objects.filter(
            token_hash=token_hash,
            use_count__lt=F("max_redemptions"),
            expires_at__gt=now,
        ).update(use_count=F("use_count") + 1)
        if rows == 0:
            # Either missing, exhausted, or expired — re-fetch to give a precise
            # exception. Missing rows are treated as permissive (no DB-backing
            # ever existed) to stay compatible with stateless legacy tokens.
            exists = model.objects.filter(token_hash=token_hash).exists()
            if exists:
                raise signing.SignatureExpired("Token exhausted or expired")


def create_pending_registration_token(
    *,
    email: str,
    mode: str,
    registration_context: dict[str, Any] | None = None,
    access_code: str | None = None,
    qr_signup_token: str | None = None,
) -> str:
    """S13: sign a pending-registration payload (no DB user yet).

    Lifetime is fixed at 24h. The token is verified at ``register_confirm`` time;
    only then does the actual ``User`` get created. Until then, the registration
    request leaves no DB trace, eliminating the pre-squat attack surface.
    """
    expires_at = timezone.now() + timezone.timedelta(
        seconds=PENDING_REGISTRATION_MAX_AGE_SECONDS
    )
    payload = {
        "schema_version": "1",
        "email": email.strip().lower(),
        "mode": mode,
        "registration_context": registration_context or {},
        "access_code": access_code or None,
        "qr_signup_token": qr_signup_token or None,
        "expires_at": expires_at.isoformat(),
    }
    return signing.dumps(payload, salt=PENDING_REGISTRATION_SALT)


def decode_pending_registration_token(token: str) -> dict[str, Any]:
    """S13: decode + validate a pending-registration token.

    Raises ``signing.SignatureExpired`` if expired, ``signing.BadSignature`` for
    any other tampering / format issues.
    """
    if not token:
        raise signing.BadSignature("Missing token")

    payload = signing.loads(
        token,
        salt=PENDING_REGISTRATION_SALT,
        max_age=PENDING_REGISTRATION_MAX_AGE_SECONDS,
    )
    expires_raw = payload.get("expires_at")
    expires_at = parse_datetime(expires_raw) if isinstance(expires_raw, str) else None
    if expires_at is None:
        raise signing.BadSignature("Missing expiry")
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
    if expires_at <= timezone.now():
        raise signing.SignatureExpired("Pending registration token expired")
    return payload
