"""Shared driver response helpers."""

from __future__ import annotations

from typing import Any

from ..types import TokenUsage


def usage_from_response(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    return TokenUsage(input_tokens, output_tokens, total_tokens)


def merge_passthrough(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    overlap = base.keys() & extra.keys()
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ValueError(f"Provider passthrough cannot replace normalized fields: {names}")
    return {**base, **extra}
