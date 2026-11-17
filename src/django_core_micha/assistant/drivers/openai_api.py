"""OpenAI Responses API driver with native function calls."""
import asyncio
import json
import math
import logging
from pathlib import PurePosixPath
from .base import (DEFAULT_MAX_OUTPUT_TOKENS, DriverError, arguments_json, invoke_tool,
                   map_effort, tool_specs, _reserve, log_provider_error,
                   release_unstarted, charge_aborted, estimate_tokens, _definitively_unbilled)
from django_core_micha.extraction.cost_guard import release_reservation, settle_reservation

log = logging.getLogger(__name__)


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _execution_cents(config):
    return max(1, math.ceil(config.reserved_usd_micros / 10_000))


def get_limit_execution_text():
    from ..limits import get_limit
    return get_limit("max_output_bytes")


async def _upload_execution_inputs(client, config, authorize=None):
    file_ids = []
    try:
        for dataset in config.datasets:
            if authorize and not await authorize():
                raise DriverError("access_lost")
            uploaded = await client.files.create(file=(dataset.name, dataset.content, dataset.media_type), purpose="assistants")
            file_ids.append(str(uploaded.id))
        return file_ids
    except BaseException:
        for file_id in file_ids:
            try:
                async with asyncio.timeout(3):
                    await client.files.delete(file_id)
            except Exception:
                log.warning("Hosted execution input cleanup failed driver=openai_api")
        raise


async def _cleanup_execution(client, container_id, file_ids):
    # Bounded, best-effort cleanup; provider expiry remains the final backstop.
    if container_id:
        try:
            async with asyncio.timeout(3):
                await client.containers.delete(container_id)
        except Exception: log_provider_error("openai_api", "execution_cleanup", Exception("container cleanup failed"))
    for file_id in file_ids:
        try:
            async with asyncio.timeout(3):
                await client.files.delete(file_id)
        except Exception: log_provider_error("openai_api", "execution_cleanup", Exception("file cleanup failed"))


async def _download_execution_files(client, container_id, outputs, input_ids):
    from ..execution import _limits, validate_outputs
    caps = _limits()
    file_ids = []
    for output in outputs:
        if _get(output, "type") in {"files", "file"}:
            rows = _get(output, "files", []) or [_get(output, "file", output)]
            for row in rows:
                file_id = _get(row, "file_id") or _get(row, "id")
                if isinstance(file_id, str) and file_id not in input_ids and file_id not in file_ids:
                    file_ids.append(file_id)
    if len(file_ids) > caps["max_execution_output_files"]:
        raise DriverError("execution_output_invalid")
    files = []
    for file_id in file_ids:
        metadata = await client.containers.files.retrieve(file_id, container_id=container_id)
        path = _get(metadata, "path") or _get(metadata, "name") or ""
        name = PurePosixPath(path).name
        if _get(metadata, "container_id", container_id) != container_id or _get(metadata, "source", "assistant") != "assistant":
            raise DriverError("execution_output_invalid")
        media_type = {"csv": "text/csv", "json": "application/json"}.get(name.rsplit(".", 1)[-1].lower(), "")
        size = _get(metadata, "bytes") or _get(metadata, "size")
        if type(size) is not int or size <= 0 or size > caps["max_execution_output_file_bytes"]:
            raise DriverError("execution_output_invalid")
        body = await client.containers.files.content.retrieve(file_id, container_id=container_id)
        content = body.read()
        if hasattr(content, "__await__"):
            content = await content
        files.append({"name": str(name or ""), "media_type": str(media_type or ""), "content": bytes(content)})
    return validate_outputs(files), file_ids


class OpenAIDriver:
    def __init__(self, client=None):
        self.client = client

    async def stream(self, *, model, messages, system="", tools=(), user=None, chat=None,
                     effort="medium", cancel_event=None, max_tool_calls=12, response_schema=None, **kwargs):
        if self.client is None:
            raise DriverError("not_configured")
        billing_reference = str(kwargs.pop("billing_reference", "") or getattr(chat, "pk", "") or "")
        billing_purpose = str(kwargs.pop("billing_purpose", "chat_aborted") or "chat_aborted")
        billing_source_id = str(kwargs.pop("source_id", "") or "")
        tool_map = {t.name: t for t in tools}
        input_rows = list(messages)
        if system:
            input_rows.insert(0, {"role": "system", "content": system})
        specs = [{"type": "function", "name": t["name"], "description": t["description"],
                  "parameters": t["parameters"], "strict": True} for t in tool_specs(tools)]
        execution = kwargs.pop("execution", None)
        authorize = kwargs.pop("authorize", None)
        session = execution.session if execution is not None else None
        container_id = session.container_id if session else None
        uploaded_ids = list(session.input_file_ids) if session else []
        execution_reservation = session.reservation if session else None
        execution_started = bool(session and session.possible_charge)
        execution_possible = bool(session and session.possible_charge)
        execution_reported = bool(session and session.cost_reported)
        execution_records = session.execution_records if session else set()
        execution_code_deltas = {}
        execution_calls = session.execution_calls if session else 0
        calls = 0
        try:
            if cancel_event and cancel_event.is_set():
                raise DriverError("cancelled")
            if execution is not None:
                from django_core_micha.extraction.cost_guard import reserve_user_budget, settle_fixed_reservation
                if not session.reservation_attempted:
                    if authorize and not await authorize():
                        raise DriverError("access_lost")
                    try:
                        session.reservation = reserve_user_budget(user_id=getattr(user, "pk", None), cents=_execution_cents(execution))
                        session.reservation_attempted = True
                        execution_reservation = session.reservation
                    except Exception as exc:
                        if getattr(exc, "code", None) == "quota_exceeded" or type(exc).__name__ == "AICostLimitExceeded":
                            raise DriverError("quota_exceeded") from exc
                        raise DriverError("accounting_unavailable") from exc
                if not session.container_id:
                    uploaded_ids = await _upload_execution_inputs(self.client, execution, authorize)
                    session.input_file_ids = list(uploaded_ids)
                    if authorize and not await authorize():
                        raise DriverError("access_lost")
                    session.possible_charge = True
                    execution_possible = True
                    try:
                        container = await self.client.containers.create(
                            name=f"dcm-assistant-{getattr(chat, 'pk', 'analysis')}",
                            file_ids=uploaded_ids, memory_limit=execution.memory_limit,
                            network_policy={"type": "disabled"},
                            expires_after={"anchor": "last_active_at", "minutes": 20},
                        )
                    except BaseException as exc:
                        if _definitively_unbilled(exc):
                            session.possible_charge = execution_possible = False
                        raise
                    container_id = str(container.id)
                    session.container_id = container_id
                    session.possible_charge = True
                    execution_started = True
                    if authorize and not await authorize():
                        raise DriverError("access_lost")
                else:
                    container_id = session.container_id
                    uploaded_ids = list(session.input_file_ids)
                    execution_possible = session.possible_charge
                    execution_started = session.possible_charge
                input_rows.insert(0, {"role": "system", "content": (
                    "Use the hosted code interpreter only for bounded analysis. When returning generated data files, "
                    "write only CSV or JSON files as analysis outputs; do not create or return executable content.")})
            while True:
                if cancel_event and cancel_event.is_set():
                    raise DriverError("cancelled")
                prompt_bytes = len(json.dumps(input_rows, ensure_ascii=False).encode())
                # Responses remains uncapped; reserve using the standard ceiling.
                reservation = _reserve(getattr(user, "pk", None), model, prompt_bytes, DEFAULT_MAX_OUTPUT_TOKENS)
                input_estimate, output_estimate = estimate_tokens(prompt_bytes, DEFAULT_MAX_OUTPUT_TOKENS)
                function_calls = {}
                final_usage = None
                completed_response = False
                buffered_text = []
                started = False
                try:
                    if execution is not None and authorize and not await authorize():
                        raise DriverError("access_lost")
                    started = True
                    provider_tools = list(specs)
                    if execution is not None:
                        provider_tools.append({"type": "code_interpreter", "container": container_id,
                            "allowed_callers": ["direct"]})
                    request = {"model": model, "input": input_rows, "stream": True,
                        "tools": provider_tools or None, "reasoning": {"effort": map_effort("openai_api", effort)}}
                    if execution is not None:
                        request["max_tool_calls"] = min(max_tool_calls, execution.max_continuations)
                    if response_schema is not None:
                        request["text"] = {"format": {"type": "json_schema", "name": "completion",
                            "strict": True, "schema": response_schema}}
                    stream = await self.client.responses.create(**request)
                    async for event in stream:
                        kind = getattr(event, "type", "")
                        if kind == "response.output_text.delta":
                            if execution is None:
                                yield {"type": "delta", "text": event.delta}
                            else:
                                buffered_text.append(event.delta)
                                if sum(len(part.encode("utf-8")) for part in buffered_text) > get_limit_execution_text():
                                    raise DriverError("execution_output_invalid")
                        elif kind == "response.code_interpreter_call_code.delta":
                            item_id = str(getattr(event, "item_id", ""))
                            content = execution_code_deltas.get(item_id, "") + str(getattr(event, "delta", ""))
                            from ..execution import _limits
                            if len(content.encode("utf-8")) > _limits()["max_execution_text_bytes"]:
                                raise DriverError("execution_output_invalid")
                            execution_code_deltas[item_id] = content
                        elif kind == "response.function_call_arguments.delta":
                            function_calls.setdefault(getattr(event, "item_id", ""), {"id": getattr(event, "item_id", ""),
                                "name": getattr(event, "name", ""), "arguments": ""})["arguments"] += event.delta
                        elif kind == "response.output_item.added" and getattr(getattr(event, "item", None), "type", None) == "function_call":
                            item = event.item
                            function_calls[item.id] = {"id": item.call_id, "name": item.name,
                                "arguments": getattr(item, "arguments", "")}
                        elif kind == "response.completed":
                            response = getattr(event, "response", None)
                            completed_response = response is not None
                            final_usage = getattr(response, "usage", None)
                            output = getattr(response, "output", []) or []
                            citations = []
                            for message in output:
                                if _get(message, "type") != "message":
                                    continue
                                for content in _get(message, "content", []) or []:
                                    for annotation in _get(content, "annotations", []) or []:
                                        if _get(annotation, "type") == "container_file_citation":
                                            if _get(annotation, "container_id") != container_id:
                                                raise DriverError("execution_output_invalid")
                                            citations.append({"file_id": _get(annotation, "file_id")})
                            citation_files = []
                            citation_ids = []
                            output_ids = []
                            if citations:
                                citation_files, citation_ids = await _download_execution_files(
                                    self.client, container_id, [{"type": "files", "files": citations}], uploaded_ids)
                            for item in output:
                                if getattr(item, "type", None) == "function_call":
                                    function_calls[item.id] = {"id": item.call_id, "name": item.name,
                                        "arguments": getattr(item, "arguments", "")}
                                elif getattr(item, "type", None) == "code_interpreter_call":
                                    call_id = str(getattr(item, "id", ""))
                                    if call_id and call_id not in execution_records:
                                        execution_records.add(call_id)
                                        execution_calls += 1
                                        if session:
                                            session.execution_calls = execution_calls
                                        if execution_calls > execution.max_continuations if execution else False:
                                            raise DriverError("execution_limit")
                                        if getattr(item, "container_id", container_id) != container_id:
                                            raise DriverError("security_violation")
                                        from ..execution import _limits, normalize_text, redact_provider_refs
                                        call_outputs = list(getattr(item, "outputs", []) or [])
                                        logs = "\n".join(str(_get(row, "logs", "")) for row in call_outputs if _get(row, "type") == "logs")
                                        files, file_ids = await _download_execution_files(self.client, container_id, call_outputs, uploaded_ids)
                                        output_ids.extend(file_ids)
                                        code = getattr(item, "code", "") or execution_code_deltas.get(call_id, "")
                                        if citation_files:
                                            files.extend(citation_files)
                                            citation_files = []
                                        if len(files) > _limits()["max_execution_output_files"] or len(json.dumps(files, ensure_ascii=False).encode("utf-8")) > _limits()["max_execution_output_total_bytes"]:
                                            raise DriverError("execution_output_invalid")
                                        record = {"code": normalize_text(str(code), _limits()["max_execution_text_bytes"]),
                                            "status": "completed" if getattr(item, "status", "completed") == "completed" else "failed", "output_text": normalize_text(logs, _limits()["max_execution_text_bytes"]),
                                            "data": files}
                                        record = redact_provider_refs(record, [container_id, call_id, *uploaded_ids, *file_ids, *citation_ids])
                                        yield {"type": "execution", "record": record}
                                        input_rows.append(item.model_dump(exclude_none=True))
                            if citation_files:
                                raise DriverError("execution_output_invalid")
                            if execution is not None and buffered_text:
                                from ..execution import redact_provider_refs
                                yield {"type": "delta", "text": redact_provider_refs("".join(buffered_text), [container_id, *uploaded_ids, *citation_ids, *output_ids, *execution_records])}
                    if not completed_response:
                        raise DriverError("invalid_output")
                    settle_reservation(reservation=reservation,
                        response=type("R", (), {"usage": final_usage})(), model=model)
                except BaseException as exc:
                    if not started or _definitively_unbilled(exc):
                        release_unstarted(reservation)
                    else:
                        await charge_aborted(reservation, user=user, provider="openai_api",
                            model=model, input_tokens_estimate=input_estimate,
                            output_tokens_estimate=output_estimate,
                            reference=billing_reference, purpose=billing_purpose,
                            source_id=billing_source_id, exc=exc)
                    raise
                from types import SimpleNamespace
                from django_core_micha.extraction.drivers._common import usage_from_response
                normalized_usage = usage_from_response(SimpleNamespace(usage=final_usage))
                yield {"type": "usage", "usage": {"input_tokens": normalized_usage.input_tokens,
                        "output_tokens": normalized_usage.output_tokens,
                        "cache_read_tokens": normalized_usage.cache_read_tokens,
                        "cache_write_tokens": normalized_usage.cache_write_tokens,
                        **({"execution_usd_micros": execution.reserved_usd_micros if execution_started and not execution_reported else 0} if execution is not None else {})},
                    "usage_reported": normalized_usage.reported}
                if execution_started:
                    execution_reported = True
                    if session:
                        session.cost_reported = True
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
        except DriverError as exc:
            if execution_possible and not execution_reported:
                exc.execution_cost_estimate = execution.reserved_usd_micros
            raise
        except Exception as exc:
            if execution_possible and not execution_reported:
                exc.execution_cost_estimate = execution.reserved_usd_micros
            if execution is None:
                log_provider_error("openai_api", model, exc)
            else:
                log.warning("Hosted execution provider failed driver=openai_api type=%s", type(exc).__name__)
            code = "quota_exceeded" if getattr(exc, "status_code", None) == 429 else "provider_error"
            error = DriverError(code)
            if hasattr(exc, "execution_cost_estimate"):
                error.execution_cost_estimate = exc.execution_cost_estimate
            raise error from exc
        except BaseException as exc:
            if execution_possible and not execution_reported:
                try: exc.execution_cost_estimate = execution.reserved_usd_micros
                except Exception: pass
            raise
        finally:
            if session and execution_possible:
                session.possible_charge = True

    async def finish_execution(self, execution):
        """Finalize the one provider session owned by one Assistant turn."""
        session = execution.session
        if session.finalized:
            return
        session.finalized = True
        from django_core_micha.extraction.cost_guard import release_reservation, settle_fixed_reservation
        if session.reservation_attempted:
            if session.possible_charge:
                settle_fixed_reservation(reservation=session.reservation)
            else:
                release_reservation(reservation=session.reservation)
        if session.container_id or session.input_file_ids:
            await _cleanup_execution(self.client, session.container_id, session.input_file_ids)
