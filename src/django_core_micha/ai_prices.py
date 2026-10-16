"""Shared provider token pricing in USD per million tokens."""
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


_DEFAULT_PRICES = {
    "claude-fable-5-1": {"input": 10, "output": 50, "cache_read": 0.25, "cache_write": 12.5},
    "claude-opus-5-5": {"input": 4, "output": 20, "cache_read": 0.20, "cache_write": 5},
    "claude-sonnet-5-5": {"input": 2, "output": 10, "cache_read": 0.10, "cache_write": 2.5},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125,
                          "tiers": [{"above_prompt_tokens": 100000, "input": 0.50, "output": 2.50,
                                     "cache_read": 0.05, "cache_write": 0.625}]},
    "gpt-6-astra": {"input": 10, "output": 50},
    "gpt-6.1-sol": {"input": 2, "output": 10, "cache_read": 0.10},
    "gpt-6-luna": {"input": 0.10, "output": 0.50},
}


def get_price_table():
    """Return the configured table, replacing defaults as a whole when set."""
    return getattr(settings, "AI_MODEL_PRICES_USD_PER_MILLION", _DEFAULT_PRICES)


def _legacy_rates(model):
    from django_core_micha.extraction.cost_guard import (
        _FALLBACK_COST_PER_1K_INPUT, _FALLBACK_COST_PER_1K_OUTPUT, get_cost_table,
    )
    cents = get_cost_table()
    if isinstance(cents, dict) and model in cents:
        row = cents[model]
        # cents / 1K tokens -> USD / 1M tokens
        return {"input": Decimal(str(row.get("input", _FALLBACK_COST_PER_1K_INPUT))) * 10,
                "output": Decimal(str(row.get("output", _FALLBACK_COST_PER_1K_OUTPUT))) * 10}
    return {"input": Decimal(str(_FALLBACK_COST_PER_1K_INPUT)) * 10,
            "output": Decimal(str(_FALLBACK_COST_PER_1K_OUTPUT)) * 10}


def price_usage(model, *, input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0):
    """Price normalized usage and return rounded integer micro-USD."""
    table = get_price_table()
    row = table.get(model) if isinstance(table, dict) else None
    if not isinstance(row, dict):
        rates = _legacy_rates(model)
    else:
        rates = dict(row)
        prompt = int(input_tokens or 0) + int(cache_read_tokens or 0) + int(cache_write_tokens or 0)
        for tier in sorted(row.get("tiers", ()), key=lambda item: int(item.get("above_prompt_tokens", 0))):
            if prompt > int(tier.get("above_prompt_tokens", 0)):
                rates.update(tier)
    if rates.get("input") is None or rates.get("output") is None:
        legacy = _legacy_rates(model)  # an incomplete row never silently prices at 0
        rates.setdefault("input", legacy["input"]); rates.setdefault("output", legacy["output"])
        if rates["input"] is None: rates["input"] = legacy["input"]
        if rates["output"] is None: rates["output"] = legacy["output"]
    input_rate = Decimal(str(rates["input"]))
    output_rate = Decimal(str(rates["output"]))
    read_rate = Decimal(str(rates.get("cache_read", input_rate)))
    write_rate = Decimal(str(rates.get("cache_write", input_rate)))
    amount = (int(input_tokens or 0) * input_rate + int(output_tokens or 0) * output_rate
              + int(cache_read_tokens or 0) * read_rate + int(cache_write_tokens or 0) * write_rate)
    return int((amount / Decimal(1_000_000) * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
