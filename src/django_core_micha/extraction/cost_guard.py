"""Daily AI cost accounting and pre-provider budget admission.

Settled usage stays in the established per-user UTC-day counter. Extraction
admission adds a conservative, expiring reservation before the provider runs;
settlement moves actual usage into that same day's counter and releases the
reservation. The accounting cache is required for enabled paid extraction.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)


class AICostLimitExceeded(Exception):
    """Raised when settled spend plus outstanding reservations reaches the cap."""

    def __init__(self, *, user_id: int, used_cents: int, limit_cents: int):
        self.user_id = user_id
        self.used_cents = used_cents
        self.limit_cents = limit_cents
        super().__init__(
            f"Daily AI cost limit reached for user {user_id}: "
            f"{used_cents}¢ used / {limit_cents}¢ cap"
        )


class AICostAccountingUnavailable(Exception):
    """Raised when a paid extraction cannot be safely admitted by the cache."""


@dataclass(frozen=True)
class _Reservation:
    user_id: int
    day: str
    bucket: int
    cents: int


_FALLBACK_COST_PER_1K_INPUT = 1.5
_FALLBACK_COST_PER_1K_OUTPUT = 6.0
_DEFAULT_MODEL_COSTS_CENTS_PER_1K = {
    "gpt-4o-mini": {"input": 0.015, "output": 0.060},
    "gpt-4o": {"input": 0.25, "output": 1.0},
    "gpt-4.1-mini": {"input": 0.04, "output": 0.16},
    "gpt-4.1": {"input": 0.2, "output": 0.8},
    # Claude Sonnet 5 list price: $3 input / $15 output per 1M tokens.
    "claude-sonnet-5": {"input": 0.3, "output": 1.5},
}

# A bucket expires after the maximum reservation age plus one bucket width.
_RESERVATION_LIFETIME_SECONDS = 30 * 60
_RESERVATION_BUCKET_SECONDS = 5 * 60


def _get_cost_table():
    """Return the operator-configured rate table, or the safe defaults."""
    return getattr(settings, "AI_MODEL_COST_TABLE_CENTS_PER_1K", _DEFAULT_MODEL_COSTS_CENTS_PER_1K)


def estimate_cost_cents(*, model: str, input_tokens: int, output_tokens: int) -> int:
    """Estimate cost in integer cents, rounded up with a one-cent floor."""
    table = _get_cost_table()
    if isinstance(table, dict) and model in table:
        entry = table[model]
        cost_in = (input_tokens / 1000.0) * float(entry.get("input", _FALLBACK_COST_PER_1K_INPUT))
        cost_out = (output_tokens / 1000.0) * float(entry.get("output", _FALLBACK_COST_PER_1K_OUTPUT))
    else:
        cost_in = (input_tokens / 1000.0) * _FALLBACK_COST_PER_1K_INPUT
        cost_out = (output_tokens / 1000.0) * _FALLBACK_COST_PER_1K_OUTPUT
    return max(1, int(cost_in + cost_out + 0.999))


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _cache_key_for_day(user_id: int, day: str) -> str:
    env = getattr(settings, "ENV_TYPE", "local")
    return f"{env}:ai:cost:{user_id}:{day}"


def _cache_key(user_id: int) -> str:
    return _cache_key_for_day(user_id, _today_key())


def _reservation_prefix(user_id: int, day: str) -> str:
    env = getattr(settings, "ENV_TYPE", "local")
    return f"{env}:ai:reservation:{user_id}:{day}"


def _reservation_key(user_id: int, day: str, bucket: int) -> str:
    return f"{_reservation_prefix(user_id, day)}:{bucket}"


def _is_enabled() -> bool:
    default = not getattr(settings, "IS_LOCAL", False)
    return getattr(settings, "AI_COST_LIMIT_ENABLED", default)


def _ttl_seconds() -> int:
    return 60 * 60 * 26


def _reservation_ttl_seconds() -> int:
    return max(60, int(getattr(settings, "AI_COST_RESERVATION_TTL_SECONDS", _RESERVATION_LIFETIME_SECONDS)))


def _reservation_buckets(now: float, ttl: int) -> range:
    current = int(now // _RESERVATION_BUCKET_SECONDS)
    count = (ttl + _RESERVATION_BUCKET_SECONDS - 1) // _RESERVATION_BUCKET_SECONDS
    return range(max(0, current - count), current + 1)


def _read_counter(key: str) -> int:
    value = cache.get(key, 0)
    return max(0, int(value or 0))


def _reservation_estimate(request, model: str) -> int:
    """Estimate prompt and bounded input bytes without reserving the whole cap."""
    text = f"{request.system_prompt}\n{request.user_prompt}\n{request.schema!r}\n{request.pdf_text or ''}"
    input_tokens = max(1, (len(text) + 3) // 4)
    payload = request.image_bytes if request.image_bytes is not None else request.upload_bytes
    if payload:
        # Binary/image tokenization varies by provider; use a conservative capped
        # byte heuristic so large uploads do not reserve an unbounded amount.
        input_tokens += min(40_000, (len(payload) + 2) // 3)
    output_tokens = max(1, int(request.max_output_tokens))
    return estimate_cost_cents(model=model, input_tokens=input_tokens, output_tokens=output_tokens)


def reserve_user_budget(*, user_id: int | None, cents: int) -> _Reservation | None:
    """Atomically reserve cents against settled spend and live reservations.

    Lock-free: the reservation is added with an atomic ``add``/``incr`` FIRST and
    the cap is checked against the resulting totals afterwards; a denied request
    rolls its own cents back. Concurrent requests that both see the cap exceeded
    may both be denied (conservative), but the admitted set can never exceed it.
    """
    if not _is_enabled() or user_id is None or cents <= 0:
        return None
    user_id = int(user_id)
    day = _today_key()
    now = time.time()
    bucket = int(now // _RESERVATION_BUCKET_SECONDS)
    ttl = _reservation_ttl_seconds()
    reservation = _Reservation(user_id, day, bucket, cents)
    key = _reservation_key(user_id, day, bucket)
    bucket_ttl = ttl + _RESERVATION_BUCKET_SECONDS
    added = False
    try:
        if not cache.add(key, cents, timeout=bucket_ttl):
            try:
                cache.incr(key, cents)
            except ValueError:  # bucket expired between add and incr
                if not cache.add(key, cents, timeout=bucket_ttl):
                    cache.incr(key, cents)
        added = True
        settled = _read_counter(_cache_key_for_day(user_id, day))
        reserved = sum(
            _read_counter(_reservation_key(user_id, day, item))
            for item in _reservation_buckets(now, ttl)
        )
        limit = int(getattr(settings, "AI_DAILY_COST_LIMIT_CENTS", 500))
        if settled + reserved > limit:
            _decrement_reservation(reservation)
            raise AICostLimitExceeded(
                user_id=user_id, used_cents=settled + reserved - cents, limit_cents=limit
            )
        return reservation
    except AICostLimitExceeded:
        raise
    except Exception as exc:
        if added:
            _decrement_reservation(reservation)
        raise AICostAccountingUnavailable("AI budget accounting cache is unavailable.") from exc


def reserve_for_extraction(*, user_id: int | None, request, model: str) -> _Reservation | None:
    """Estimate and reserve one extraction before invoking a provider."""
    if not _is_enabled() or user_id is None:
        return None
    try:
        cents = _reservation_estimate(request, model)
    except Exception as exc:
        raise AICostAccountingUnavailable("Could not estimate AI extraction cost.") from exc
    return reserve_user_budget(user_id=user_id, cents=cents)


def _response_cost_cents(*, response, model: str) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return estimate_cost_cents(model=model, input_tokens=1000, output_tokens=1000)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return estimate_cost_cents(model=model, input_tokens=input_tokens, output_tokens=output_tokens)


def _increment_day_counter(user_id: int, day: str, cents: int) -> int:
    key = _cache_key_for_day(user_id, day)
    if cache.add(key, cents, timeout=_ttl_seconds()):
        return cents
    try:
        return int(cache.incr(key, cents))
    except ValueError:
        # Retry with atomic add after an expired-key race. Callers hold the
        # per-user/day lock, so an existing key can safely be incremented.
        if cache.add(key, cents, timeout=_ttl_seconds()):
            return cents
        return int(cache.incr(key, cents))


def settle_reservation(*, reservation: _Reservation | None, response, model: str) -> int:
    """Record actual usage on admission day, release reservation, never cap-raise.

    Only atomic cache calls are used (no admission lock): the settled counter is
    incremented before the reservation is decremented, so a concurrent admission
    can only over-count transiently, never over-admit.
    """
    if reservation is None:
        return 0
    try:
        actual = _response_cost_cents(response=response, model=model)
        total = _increment_day_counter(reservation.user_id, reservation.day, actual)
    except Exception:
        logger.exception("Could not settle AI extraction accounting")
        return 0
    _decrement_reservation(reservation)
    return total


def _decrement_reservation(reservation: _Reservation) -> None:
    try:
        cache.decr(
            _reservation_key(reservation.user_id, reservation.day, reservation.bucket),
            reservation.cents,
        )
    except ValueError:
        pass  # The bounded reservation already expired.
    except Exception:
        logger.exception("Could not release AI extraction reservation")


def release_reservation(*, reservation: _Reservation | None) -> None:
    """Release definitely unbilled work; cache failures leave bounded expiry."""
    if reservation is not None:
        _decrement_reservation(reservation)


def charge_user(user_id: int | None, cents: int) -> int:
    """Atomically add cents to a user's daily counter and enforce the cap."""
    if not _is_enabled() or user_id is None or cents <= 0:
        return 0
    key = _cache_key(int(user_id))
    limit = int(getattr(settings, "AI_DAILY_COST_LIMIT_CENTS", 500))
    if cache.add(key, cents, timeout=_ttl_seconds()):
        new_total = cents
    else:
        try:
            new_total = cache.incr(key, cents)
        except ValueError:
            cache.set(key, cents, timeout=_ttl_seconds())
            new_total = cents
    if new_total > limit:
        logger.warning("AI cost-cap triggered: user=%s used=%s¢ limit=%s¢", user_id, new_total, limit)
        raise AICostLimitExceeded(user_id=int(user_id), used_cents=int(new_total), limit_cents=limit)
    return int(new_total)


def charge_user_for_response(*, user_id: int | None, response, model: str) -> int:
    """Charge from a normalized or provider response's token usage."""
    return charge_user(user_id, _response_cost_cents(response=response, model=model))


def current_user_spend_cents(user_id: int) -> int:
    """Return the current UTC day's recorded settled spend for a user."""
    value = cache.get(_cache_key(int(user_id)), 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
