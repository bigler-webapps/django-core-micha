"""Shared driver response helpers."""

from __future__ import annotations

from typing import Any

from ..types import TokenUsage


def usage_from_response(response: Any) -> TokenUsage:
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if usage is None:
        return TokenUsage(reported=False)

    def value(obj, key, default=0):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    def has(obj, key):
        return key in obj if isinstance(obj, dict) else hasattr(obj, key)

    input_details = value(usage, "input_tokens_details", {}) or {}
    prompt_details = value(usage, "prompt_tokens_details", {}) or {}
    openai_cached = max(0, int(value(input_details, "cached_tokens",
        value(prompt_details, "cached_tokens")) or 0))
    raw_input = value(usage, "input_tokens", value(usage, "prompt_tokens"))
    raw_output = value(usage, "output_tokens", value(usage, "completion_tokens"))
    input_tokens = max(0, int(raw_input or 0))
    output_tokens = max(0, int(raw_output or 0))
    cache_read = max(0, int(value(usage, "cache_read_tokens",
        value(usage, "cache_read_input_tokens", openai_cached)) or 0))
    cache_write = max(0, int(value(usage, "cache_write_tokens",
        value(usage, "cache_creation_input_tokens", 0)) or 0))
    if openai_cached:
        input_tokens = max(0, input_tokens - openai_cached)
    total_tokens = input_tokens + output_tokens + cache_read + cache_write
    fields = ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens",
              "cache_read_tokens", "cache_read_input_tokens", "cache_write_tokens",
              "cache_creation_input_tokens")
    reported = (bool(value(usage, "reported")) if has(usage, "reported") else
                any(has(usage, key) for key in fields) or
                has(input_details, "cached_tokens") or has(prompt_details, "cached_tokens"))
    return TokenUsage(input_tokens, output_tokens, total_tokens, cache_read, cache_write, reported)


def merge_passthrough(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    overlap = base.keys() & extra.keys()
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ValueError(f"Provider passthrough cannot replace normalized fields: {names}")
    return {**base, **extra}


def best_effort_delete(delete_fn) -> None:
    """Run a cleanup delete; swallow any failure so it never masks the real result."""
    try:
        delete_fn()
    except Exception:
        pass
