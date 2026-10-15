"""Shared provider errors, effort mapping, tool execution and spend accounting."""
import asyncio
import json
from dataclasses import dataclass


class DriverError(Exception):
    def __init__(self, code="provider_error"):
        self.code = code
        super().__init__(code)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


def map_effort(driver, effort):
    if effort not in {"low", "medium", "high", "max"}:
        raise DriverError("invalid_selection")
    if driver == "claude_cli": return {"low":"low","medium":"medium","high":"high","max":"high"}[effort]
    if driver == "openai_api": return {"low":"low","medium":"medium","high":"high","max":"xhigh"}[effort]
    if driver == "anthropic_api": return {"low":"low","medium":"medium","high":"high","max":"high"}[effort]
    return effort


def _reserve(user_id, model, prompt_bytes, output_tokens=2048):
    from django_core_micha.extraction.cost_guard import AICostLimitExceeded, estimate_cost_cents, reserve_user_budget
    input_tokens = max(1, (prompt_bytes + 1) // 2)
    try:
        return reserve_user_budget(user_id=user_id,
            cents=estimate_cost_cents(model=model, input_tokens=input_tokens, output_tokens=output_tokens))
    except AICostLimitExceeded as exc:
        raise DriverError("quota_exceeded") from exc


def _settle(reservation, model, usage):
    from django_core_micha.extraction.cost_guard import settle_reservation
    settle_reservation(reservation=reservation, response=type("R", (), {"usage": Usage(
        int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0))})(), model=model)


async def call_with_budget(*, user_id, model, prompt_bytes, operation):
    """Reserve before every SDK call; settle only on a final response, release on failure."""
    from django_core_micha.extraction.cost_guard import release_reservation
    reservation = _reserve(user_id, model, prompt_bytes)
    try:
        response = await operation()
        usage = getattr(response, "usage", None)
        if usage is None and hasattr(response, "get_final_message"):
            response = await response.get_final_message()
            usage = getattr(response, "usage", None)
        _settle(reservation, model, usage)
        return response
    except BaseException:
        release_reservation(reservation=reservation)
        raise


async def invoke_tool(tool, user, chat, arguments, trust_class):
    if not isinstance(arguments, dict):
        raise DriverError("invalid_output")
    try:
        result = tool.handler(user, chat, arguments, trust_class)
        if asyncio.iscoroutine(result):
            result = await result
        content, sources = result
        if not isinstance(sources, list):
            raise ValueError
        content = str(content)
        from ..limits import get_limit
        if len(content.encode("utf-8")) > get_limit("max_prompt_bytes"):
            raise DriverError("prompt_too_long")  # a tool result must not push a later provider call past the prompt bound
        return content, sources
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError("provider_error") from exc


def tool_specs(tools):
    return [{"name": t.name, "description": t.description, "parameters": t.input_schema} for t in tools]


def arguments_json(value):
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError) as exc:
        raise DriverError("invalid_output") from exc
    if not isinstance(result, dict):
        raise DriverError("invalid_output")
    return result
