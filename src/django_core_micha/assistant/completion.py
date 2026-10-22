"""Non-persistent, single provider completions for app services."""
import asyncio
import json
import re
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
from .structured import check_schema, validation_error

@dataclass(frozen=True)
class CompletionResult:
    text: str
    usage: dict
    data: object = None

async def acomplete(user, *, prompt, system="", entry=None, driver=None, model=None, effort=None, purpose="completion", response_schema=None):
    if not isinstance(prompt, str) or not prompt.strip(): raise DriverError("invalid_output")
    if len(prompt.encode("utf-8")) + len((system or "").encode("utf-8")) > get_limit("max_prompt_bytes"): raise DriverError("prompt_too_long")
    if response_schema is not None and not check_schema(response_schema): raise DriverError("invalid_schema")
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
    structured_data = None
    try:
        provider = _make_driver(driver, credential)
        rounds = get_limit("max_repair_rounds") if response_schema is not None else 0
        current_messages = [{"role": "user", "content": prompt}]
        current_prompt = prompt
        for attempt in range(rounds + 1):
            chunks, call_usage, structured_data = [], {}, None
            payload = json.dumps({"system":system,"message":current_prompt},ensure_ascii=False)
            async with asyncio.timeout(get_limit("provider_timeout")):
                async for event in provider.stream(model=model, prompt=payload, messages=current_messages,
                        system=system, tools=[], user=user, chat=client, credential=credential, effort=effort,
                        cancel_event=None, session_id=None, max_tool_calls=get_limit("max_tool_calls"),
                        response_schema=response_schema):
                    if event.get("type")=="delta":
                        chunks.append(event.get("text", ""))
                        if sum(len(part.encode("utf-8")) for part in chunks)>get_limit("max_output_bytes"):
                            raise DriverError("invalid_output")
                    elif event.get("type")=="usage":
                        call_usage.update(event.get("usage", {}) or {})
                    elif event.get("type")=="structured":
                        structured_data = event.get("data")
                        # a structured frame is output too: the same cap as text deltas
                        if len(json.dumps(structured_data, ensure_ascii=False).encode("utf-8"))>get_limit("max_output_bytes"):
                            raise DriverError("invalid_output")
            for key, value in call_usage.items():
                if key in {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cache_tokens"}:
                    usage[key] = int(usage.get(key, 0) or 0) + int(value or 0)
                else:
                    usage[key] = value
            text = "".join(chunks)
            if response_schema is None:
                break
            data = structured_data
            error = None
            if data is None:
                candidate = text.strip()
                match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", candidate, flags=re.DOTALL | re.IGNORECASE)
                if match:
                    candidate = match.group(1).strip()
                try:
                    # NaN/Infinity are not JSON; Python's parser would accept them
                    data = json.loads(candidate, parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
                except (TypeError, ValueError):
                    error = "$: answer is not valid JSON"
            if error is None and not isinstance(data, dict):
                error = "$: answer must be a JSON object"
            if error is None:
                error = validation_error(data, response_schema)
            if error is None:
                break
            if attempt >= rounds:
                raise DriverError("invalid_output")
            previous = text if text else json.dumps(data, ensure_ascii=False)
            repair = f"Your previous answer did not match the required JSON schema: {error}. Answer again with only the JSON object."
            if driver == "claude_cli":
                current_prompt = f"{prompt}\n\n{previous}\n\n{repair}"
            else:
                current_messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": previous}, {"role": "user", "content": repair}]
        result = CompletionResult(text, usage, structured_data if response_schema is None else data)
        from django.apps import apps
        if usage and apps.is_installed("django_core_micha.billing"):
            try:
                from asgiref.sync import sync_to_async
                from django_core_micha.billing.services import record_ai_call
                await sync_to_async(record_ai_call, thread_sensitive=True)(user=user, provider=driver, model=model, input_tokens=int(usage.get("input_tokens",0) or 0),
                    output_tokens=int(usage.get("output_tokens",0) or 0),
                    cache_read_tokens=int(usage.get("cache_read_tokens",0) or 0),
                    cache_write_tokens=int(usage.get("cache_write_tokens",0) or 0),
                    cache_tokens=int(usage.get("cache_tokens",0) or 0),
                    purpose=purpose, reference=str(getattr(entry,"pk","")) or f"completion:{__import__('uuid').uuid4()}")
            except Exception:
                import logging
                logging.getLogger(__name__).error("Completion billing cost hook failed (%s)", __import__("sys").exc_info()[0].__name__)
        return result
    except TimeoutError as exc: raise DriverError("timeout") from exc
    except DriverError: raise
    except Exception as exc: raise DriverError(getattr(exc,"code","provider_error")) from exc
    finally:
        if cleanup is not None: shutil.rmtree(cleanup,ignore_errors=True)

def complete(user, **kwargs):
    return async_to_sync(acomplete)(user, **kwargs)
