"""S193 -- daily per-user AI cost cap.

The charge is intentionally made after a provider response, when actual token
usage is known. The cents have already been incremented when the limit error is
raised: the over-by-one request is accepted as already spent, and the persisted
over-limit counter blocks the next request.

The counter uses an atomic cache ``add``-then-``incr`` seed path. Non-local
deployments must use a cache backend whose ``incr`` is atomic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)


class AICostLimitExceeded(Exception):
    """Raised after a charge takes a user's daily counter over its cap."""

    def __init__(self, *, user_id: int, used_cents: int, limit_cents: int):
        self.user_id = user_id
        self.used_cents = used_cents
        self.limit_cents = limit_cents
        super().__init__(
            f"Daily AI cost limit reached for user {user_id}: "
            f"{used_cents}¢ used / {limit_cents}¢ cap"
        )


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


def _get_cost_table():
    """Return the operator-configured rate table, or the safe defaults."""
    return getattr(
        settings,
        "AI_MODEL_COST_TABLE_CENTS_PER_1K",
        _DEFAULT_MODEL_COSTS_CENTS_PER_1K,
    )


def estimate_cost_cents(*, model: str, input_tokens: int, output_tokens: int) -> int:
    """Estimate cost in integer cents, rounded up with a one-cent floor."""
    table = _get_cost_table()
    if isinstance(table, dict) and model in table:
        entry = table[model]
        cost_in = (input_tokens / 1000.0) * float(
            entry.get("input", _FALLBACK_COST_PER_1K_INPUT)
        )
        cost_out = (output_tokens / 1000.0) * float(
            entry.get("output", _FALLBACK_COST_PER_1K_OUTPUT)
        )
    else:
        cost_in = (input_tokens / 1000.0) * _FALLBACK_COST_PER_1K_INPUT
        cost_out = (output_tokens / 1000.0) * _FALLBACK_COST_PER_1K_OUTPUT
    total = cost_in + cost_out
    return max(1, int(total + 0.999))


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _cache_key(user_id: int) -> str:
    env = getattr(settings, "ENV_TYPE", "local")
    return f"{env}:ai:cost:{user_id}:{_today_key()}"


def _is_enabled() -> bool:
    default = not getattr(settings, "IS_LOCAL", False)
    return getattr(settings, "AI_COST_LIMIT_ENABLED", default)


def _ttl_seconds() -> int:
    return 60 * 60 * 26


def charge_user(user_id: int | None, cents: int) -> int:
    """Atomically add cents to a user's daily counter and enforce the cap."""
    if not _is_enabled() or user_id is None:
        return 0
    if cents <= 0:
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
        logger.warning(
            "AI cost-cap triggered: user=%s used=%s¢ limit=%s¢",
            user_id,
            new_total,
            limit,
        )
        raise AICostLimitExceeded(
            user_id=int(user_id),
            used_cents=int(new_total),
            limit_cents=limit,
        )
    return int(new_total)


def charge_user_for_response(*, user_id: int | None, response, model: str) -> int:
    """Charge from a normalized or provider response's token usage."""
    usage = getattr(response, "usage", None)
    if usage is None:
        cents = estimate_cost_cents(
            model=model,
            input_tokens=1000,
            output_tokens=1000,
        )
    else:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cents = estimate_cost_cents(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return charge_user(user_id, cents)


def current_user_spend_cents(user_id: int) -> int:
    """Return the current UTC day's recorded spend for a user."""
    value = cache.get(_cache_key(int(user_id)), 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
