"""Anthropic Messages driver with native tool use and per-call cost admission."""
import json
from .base import DriverError, arguments_json, call_with_budget, invoke_tool, map_effort, tool_specs


class AnthropicDriver:
    def __init__(self, client=None):
        self.client = client

    async def stream(self, *, model, messages, system="", tools=(), user=None, chat=None,
                     effort="medium", cancel_event=None, max_tool_calls=12, **kwargs):
        if self.client is None:
            raise DriverError("not_configured")
        rows = list(messages)
        specs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                 for t in tool_specs(tools)]
        tool_map = {t.name: t for t in tools}
        calls = 0
        try:
            while True:
                if cancel_event and cancel_event.is_set():
                    raise DriverError("cancelled")
                async def create():
                    return self.client.messages.stream(model=model, system=system, messages=rows,
                        max_tokens=8192, tools=specs or None,
                        output_config={"effort": map_effort("anthropic_api", effort)})
                reservation = None
                from django_core_micha.extraction.cost_guard import release_reservation, settle_reservation
                from .base import _reserve
                reservation = _reserve(getattr(user, "pk", None), model,
                                       len(json.dumps(rows, ensure_ascii=False).encode()) + len(system.encode()))
                try:
                    stream_context = await create()
                    async with stream_context as stream:
                        async for text in stream.text_stream:
                            yield {"type": "delta", "text": text}
                        msg = await stream.get_final_message()
                    settle_reservation(reservation=reservation, response=msg, model=model)
                except BaseException:
                    release_reservation(reservation=reservation)
                    raise
                usage = getattr(msg, "usage", None)
                yield {"type": "usage", "usage": {"input_tokens": getattr(usage, "input_tokens", 0),
                    "output_tokens": getattr(usage, "output_tokens", 0)}}
                content_blocks = list(getattr(msg, "content", []) or [])
                blocks = [b for b in content_blocks if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_use"]
                if not blocks:
                    return
                def plain(block):
                    if isinstance(block, dict): return block
                    if hasattr(block, "model_dump"): return block.model_dump(exclude_none=True)
                    return {key: getattr(block, key) for key in ("type", "id", "name", "input", "text") if hasattr(block, key)}
                rows.append({"role": "assistant", "content": [plain(block) for block in content_blocks]})
                for block in blocks:
                    calls += 1
                    name = block.get("name") if isinstance(block, dict) else block.name
                    block_id = block.get("id") if isinstance(block, dict) else block.id
                    arguments = block.get("input", {}) if isinstance(block, dict) else block.input
                    if calls > max_tool_calls or name not in tool_map:
                        raise DriverError("security_violation")
                    tool = tool_map[name]
                    yield {"type": "tool", "phase": "start", "name": tool.name, "label": tool.label}
                    content, sources = await invoke_tool(tool, user, chat, arguments, "cloud")
                    yield {"type": "tool", "phase": "end", "name": tool.name, "label": tool.label, "sources": sources}
                    rows.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": block_id,
                        "content": content}]})
        except DriverError:
            raise
        except Exception as exc:
            code = "quota_exceeded" if getattr(exc, "status_code", None) == 429 else "provider_error"
            raise DriverError(code) from exc
