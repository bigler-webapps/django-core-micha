"""OpenAI Responses API driver with native function calls."""
import json
from .base import (DEFAULT_MAX_OUTPUT_TOKENS, DriverError, arguments_json, invoke_tool,
                   map_effort, tool_specs, _reserve, log_provider_error,
                   release_unstarted, charge_aborted, estimate_tokens, _definitively_unbilled)
from django_core_micha.extraction.cost_guard import release_reservation, settle_reservation


class OpenAIDriver:
    def __init__(self, client=None):
        self.client = client

    async def stream(self, *, model, messages, system="", tools=(), user=None, chat=None,
                     effort="medium", cancel_event=None, max_tool_calls=12, **kwargs):
        if self.client is None:
            raise DriverError("not_configured")
        tool_map = {t.name: t for t in tools}
        input_rows = list(messages)
        if system:
            input_rows.insert(0, {"role": "system", "content": system})
        specs = [{"type": "function", "name": t["name"], "description": t["description"],
                  "parameters": t["parameters"], "strict": True} for t in tool_specs(tools)]
        calls = 0
        try:
            while True:
                if cancel_event and cancel_event.is_set():
                    raise DriverError("cancelled")
                prompt_bytes = len(json.dumps(input_rows, ensure_ascii=False).encode())
                # Responses remains uncapped; reserve using the standard ceiling.
                reservation = _reserve(getattr(user, "pk", None), model, prompt_bytes, DEFAULT_MAX_OUTPUT_TOKENS)
                input_estimate, output_estimate = estimate_tokens(prompt_bytes, DEFAULT_MAX_OUTPUT_TOKENS)
                function_calls = {}
                final_usage = None
                started = False
                try:
                    started = True
                    stream = await self.client.responses.create(model=model, input=input_rows, stream=True,
                        tools=specs or None, reasoning={"effort": map_effort("openai_api", effort)})
                    async for event in stream:
                        kind = getattr(event, "type", "")
                        if kind == "response.output_text.delta":
                            yield {"type": "delta", "text": event.delta}
                        elif kind == "response.function_call_arguments.delta":
                            function_calls.setdefault(getattr(event, "item_id", ""), {"id": getattr(event, "item_id", ""),
                                "name": getattr(event, "name", ""), "arguments": ""})["arguments"] += event.delta
                        elif kind == "response.output_item.added" and getattr(getattr(event, "item", None), "type", None) == "function_call":
                            item = event.item
                            function_calls[item.id] = {"id": item.call_id, "name": item.name,
                                "arguments": getattr(item, "arguments", "")}
                        elif kind == "response.completed":
                            response = getattr(event, "response", None)
                            final_usage = getattr(response, "usage", None)
                            output = getattr(response, "output", []) or []
                            for item in output:
                                if getattr(item, "type", None) == "function_call":
                                    function_calls[item.id] = {"id": item.call_id, "name": item.name,
                                        "arguments": getattr(item, "arguments", "")}
                    if final_usage is None:
                        raise DriverError("invalid_output")
                    settle_reservation(reservation=reservation, response=type("R", (), {"usage": type("U", (), {
                        "input_tokens": getattr(final_usage, "input_tokens", 0),
                        "output_tokens": getattr(final_usage, "output_tokens", 0)})()})(), model=model)
                except BaseException as exc:
                    if not started or _definitively_unbilled(exc):
                        release_unstarted(reservation)
                    else:
                        await charge_aborted(reservation, user=user, provider="openai_api",
                            model=model, input_tokens_estimate=input_estimate,
                            output_tokens_estimate=output_estimate,
                            reference=str(getattr(chat, "pk", "") or ""), exc=exc)
                    raise
                if final_usage:
                    def value(obj, key, default=0):
                        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
                    input_tokens = int(value(final_usage, "input_tokens") or 0)
                    details = value(final_usage, "input_tokens_details", {}) or {}
                    cached = int(value(details, "cached_tokens") or 0)
                    yield {"type": "usage", "usage": {"input_tokens": max(0, input_tokens - cached),
                        "output_tokens": int(value(final_usage, "output_tokens") or 0),
                        "cache_read_tokens": cached, "cache_write_tokens": 0}}
                if not function_calls:
                    return
                for call in function_calls.values():
                    calls += 1
                    if calls > max_tool_calls or call["name"] not in tool_map:
                        raise DriverError("security_violation")
                    tool = tool_map[call["name"]]
                    yield {"type": "tool", "phase": "start", "name": tool.name, "label": tool.label}
                    content, sources = await invoke_tool(tool, user, chat, arguments_json(call["arguments"]), "cloud")
                    yield {"type": "tool", "phase": "end", "name": tool.name, "label": tool.label, "sources": sources}
                    input_rows.extend([{"type": "function_call", "call_id": call["id"], "name": call["name"],
                        "arguments": call["arguments"]}, {"type": "function_call_output", "call_id": call["id"], "output": content}])
        except DriverError:
            raise
        except Exception as exc:
            log_provider_error("openai_api", model, exc)
            code = "quota_exceeded" if getattr(exc, "status_code", None) == 429 else "provider_error"
            raise DriverError(code) from exc
