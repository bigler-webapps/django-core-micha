"""Non-persistent, single provider completions for app services."""
import asyncio
import json
import shutil
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from asgiref.sync import async_to_sync
from .catalogue import entry_by_id, entries, validate
from .credentials import resolve_credential
from .drivers.base import DriverError
from .limits import get_limit
from .turns import _make_driver

@dataclass(frozen=True)
class CompletionResult:
    text: str
    usage: dict

async def acomplete(user, *, prompt, system="", entry=None, driver=None, model=None, effort=None):
    if not isinstance(prompt, str) or not prompt.strip(): raise DriverError("invalid_output")
    if len(prompt.encode("utf-8")) + len((system or "").encode("utf-8")) > get_limit("max_prompt_bytes"): raise DriverError("prompt_too_long")
    if entry is not None:
        row = entry_by_id(entry)
        if row is None: raise DriverError("invalid_selection")
        driver, model = row["driver"], row["model"]
    if not driver or not model: raise DriverError("invalid_selection")
    row = next((r for r in entries() if r.get("driver")==driver and r.get("model")==model), None)
    if row is None: raise DriverError("invalid_selection")
    effort = effort or (row.get("efforts") or ["medium"])[0]
    try: validate(driver, model, effort)
    except ValueError as exc: raise DriverError("invalid_selection") from exc
    credential, _billing = resolve_credential(user, driver)
    if driver != "claude_cli" and not credential: raise DriverError("not_configured")
    client = SimpleNamespace(pk=f"completion-{uuid.uuid4()}") if driver == "claude_cli" else None
    cleanup = None
    if client:
        from .drivers.claude_cli import _session_dirs
        cleanup = _session_dirs(client.pk)[0]
    chunks, usage = [], {}
    try:
        provider = _make_driver(driver, credential)
        payload = json.dumps({"system":system,"message":prompt},ensure_ascii=False)
        async with asyncio.timeout(get_limit("provider_timeout")):
            async for event in provider.stream(model=model, prompt=payload, messages=[{"role":"user","content":prompt}],
                    system=system, tools=[], user=user, chat=client, credential=credential, effort=effort,
                    cancel_event=None, session_id=None, max_tool_calls=get_limit("max_tool_calls")):
                if event.get("type")=="delta":
                    chunks.append(event.get("text", ""))
                    if sum(len(part.encode("utf-8")) for part in chunks)>get_limit("max_output_bytes"):
                        raise DriverError("invalid_output")
                elif event.get("type")=="usage": usage.update(event.get("usage", {}) or {})
        return CompletionResult("".join(chunks), usage)
    except TimeoutError as exc: raise DriverError("timeout") from exc
    except DriverError: raise
    except Exception as exc: raise DriverError(getattr(exc,"code","provider_error")) from exc
    finally:
        if cleanup is not None: shutil.rmtree(cleanup,ignore_errors=True)

def complete(user, **kwargs):
    return async_to_sync(acomplete)(user, **kwargs)
