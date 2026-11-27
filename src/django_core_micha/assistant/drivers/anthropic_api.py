"""Anthropic Messages driver with native tool use and per-call cost admission."""
import json
import math
import logging
import asyncio
from .base import (DEFAULT_MAX_OUTPUT_TOKENS, DriverError, arguments_json, call_with_budget,
                   invoke_tool, map_effort, tool_specs, log_provider_error,
                   release_unstarted, charge_aborted, estimate_tokens, _definitively_unbilled)

log = logging.getLogger(__name__)


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _execution_cents(config):
    return max(1, math.ceil(config.reserved_usd_micros / 10_000))


async def _download_files(client, file_ids, input_ids, session):
    from ..execution import _limits, read_bounded_response, validate_outputs
    caps = _limits()
    rows = []
    seen = set(input_ids)
    seen.update(session.provider_file_ids)
    slots = max(0, caps["max_execution_output_files"] - session.output_file_count)
    cleanup_limit = (caps["max_execution_files"] +
        caps["max_execution_output_files"] * 2)
    omitted_files = 0
    omitted_bytes = 0
    total_remaining = max(0, caps["max_execution_output_total_bytes"] - session.output_bytes)
    processing_error = None
    cleanup_tracking_exhausted = False

    def track_for_cleanup(file_id):
        if file_id in seen:
            return True
        if len(session.provider_file_ids) >= cleanup_limit:
            return False
        session.provider_file_ids.append(file_id)
        seen.add(file_id)
        return True

    async def delete_excess(file_id):
        nonlocal cleanup_tracking_exhausted
        try:
            async with asyncio.timeout(3):
                await client.beta.files.delete(file_id, betas=["files-api-2025-04-14"])
        except Exception:
            # Keep failed deletions in the bounded final-cleanup list when possible.
            if track_for_cleanup(file_id):
                return
            # At capacity, make one last bounded attempt and fail closed if it also fails.
            try:
                async with asyncio.timeout(3):
                    await client.beta.files.delete(file_id, betas=["files-api-2025-04-14"])
            except Exception:
                cleanup_tracking_exhausted = True
                log.warning("Hosted execution output cleanup retry failed driver=anthropic_api")

    for file_id in file_ids:
        if not isinstance(file_id, str) or file_id in seen:
            continue
        if processing_error is not None:
            omitted_files += 1
            omitted_bytes = None
            await delete_excess(file_id)
            continue
        if len(rows) >= slots:
            omitted_files += 1
            omitted_bytes = None
            await delete_excess(file_id)
            continue
        if not track_for_cleanup(file_id):
            omitted_files += 1
            omitted_bytes = None
            await delete_excess(file_id)
            continue
        try:
            metadata = await client.beta.files.retrieve_metadata(file_id, betas=["files-api-2025-04-14"])
            name = _get(metadata, "filename") or _get(metadata, "name") or ""
            media_type = _get(metadata, "mime_type") or "application/octet-stream"
            size = _get(metadata, "size_bytes")
            if size is None:
                size = _get(metadata, "bytes")
            if type(size) is not int or size < 0:
                raise DriverError("execution_output_invalid")
            extension = str(name).rsplit(".", 1)[-1].lower()
            if ((extension == "csv" and media_type not in {"text/csv", "application/csv"}) or
                    (extension == "json" and media_type != "application/json") or
                    (extension == "txt" and media_type != "text/plain") or
                    extension not in {"csv", "json", "txt"}):
                raise DriverError("execution_output_invalid")
            read_limit = min(caps["max_execution_output_file_bytes"], total_remaining)
            if total_remaining <= 0 or (extension == "json" and total_remaining < 4):
                omitted_files += 1
                if omitted_bytes is not None:
                    omitted_bytes += size
                continue
            if extension == "json" and size > read_limit:
                content, read_truncated = b"", True
            else:
                async with client.beta.files.with_streaming_response.download(
                        file_id, betas=["files-api-2025-04-14"]) as response:
                    content, read_truncated = await read_bounded_response(response, read_limit)
            read_truncated = read_truncated or size > len(content)
            rows.append({"name": str(name), "media_type": str(media_type), "content": content,
                "source_size": size, "read_truncated": read_truncated})
            total_remaining = max(0, total_remaining - len(content))
        except Exception as exc:
            if processing_error is None:
                processing_error = (exc if isinstance(exc, DriverError)
                    else DriverError("execution_output_invalid"))
    if cleanup_tracking_exhausted:
        raise DriverError("execution_output_invalid")
    if processing_error is not None:
        raise processing_error from None
    bounded, truncation = validate_outputs(rows, session=session, omitted_files=omitted_files,
        omitted_bytes=omitted_bytes,
        return_summary=True)
    return bounded, truncation


async def _upload_execution_inputs(client, config, authorize=None, refresh=None, cancel_event=None):
    from ..execution import mark_execution_dataset_uploaded, pending_execution_datasets, refresh_execution_config
    uploaded_ids = []
    while True:
        if cancel_event and cancel_event.is_set():
            raise DriverError("cancelled")
        config = await refresh_execution_config(config, refresh)
        pending = pending_execution_datasets(config.session, config.datasets)
        if not pending:
            return uploaded_ids, config
        dataset = pending[0]
        if authorize and not await authorize():
            raise DriverError("access_lost")
        if cancel_event and cancel_event.is_set():
            raise DriverError("cancelled")
        uploaded = await client.beta.files.upload(file=(dataset.name, dataset.content, dataset.media_type),
            betas=["files-api-2025-04-14"], expires_in_seconds=3600)
        file_id = str(uploaded.id)
        uploaded_ids.append(file_id)
        config.session.input_file_ids.append(file_id)
        config.session.provider_file_ids.append(file_id)
        mark_execution_dataset_uploaded(config.session, dataset)
        config.session.possible_charge = True


def _blocks(value):
    return list(value or []) if isinstance(value, (list, tuple)) else []


async def _stream_events(stream, execution):
    if execution is None:
        async for text in stream.text_stream:
            yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    else:
        async for event in stream:
            yield event


class AnthropicDriver:
    def __init__(self, client=None):
        self.client = client

    async def stream(self, *, model, messages, system="", tools=(), user=None, chat=None,
                     effort="medium", cancel_event=None, max_tool_calls=12,
                     max_tokens=DEFAULT_MAX_OUTPUT_TOKENS, response_schema=None, **kwargs):
        if self.client is None:
            raise DriverError("not_configured")
        billing_reference = str(kwargs.pop("billing_reference", "") or getattr(chat, "pk", "") or "")
        billing_purpose = str(kwargs.pop("billing_purpose", "chat_aborted") or "chat_aborted")
        billing_source_id = str(kwargs.pop("source_id", "") or "")
        rows = list(messages)
        specs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                 for t in tool_specs(tools)]
        tool_map = {t.name: t for t in tools}
        execution = kwargs.pop("execution", None)
        authorize = kwargs.pop("authorize", None)
        refresh_execution = kwargs.pop("refresh_execution", None)
        session = execution.session if execution is not None else None
        uploaded_ids = list(session.input_file_ids) if session else []
        provider_file_ids = list(session.provider_file_ids) if session else []
        container_id = session.container_id if session else None
        execution_reservation = session.reservation if session else None
        execution_possible = bool(session and session.possible_charge)
        execution_reported = bool(session and session.cost_reported)
        execution_calls = session.execution_calls if session else 0
        execution_records = session.execution_records if session else set()
        completed_execution_records = session.completed_execution_records if session else set()
        execution_inputs = {}
        execution_code = {}
        execution_id_by_index = {}
        calls = 0
        try:
            if cancel_event and cancel_event.is_set():
                raise DriverError("cancelled")
            if execution is not None:
                from django_core_micha.extraction.cost_guard import reserve_user_budget, release_reservation, settle_fixed_reservation
                if not session.reservation_attempted:
                    if authorize and not await authorize():
                        raise DriverError("access_lost")
                    try:
                        session.reservation = reserve_user_budget(user_id=getattr(user, "pk", None), cents=_execution_cents(execution))
                        session.reservation_attempted = True
                        execution_reservation = session.reservation
                    except Exception as exc:
                        if type(exc).__name__ == "AICostLimitExceeded":
                            raise DriverError("quota_exceeded") from exc
                        raise DriverError("accounting_unavailable") from exc
                new_upload_ids, execution = await _upload_execution_inputs(
                    self.client, execution, authorize, refresh_execution, cancel_event)
                uploaded_ids = list(session.input_file_ids)
                provider_file_ids = list(session.provider_file_ids)
                attach_ids = uploaded_ids if not container_id else new_upload_ids
                if attach_ids:
                    last = rows[-1] if rows and rows[-1].get("role") == "user" else None
                    if last:
                        content = last.get("content", "")
                        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
                        last["content"] = blocks + [{"type": "container_upload", "file_id": file_id} for file_id in attach_ids]
                    else:
                        rows.append({"role": "user", "content": [{"type": "container_upload", "file_id": file_id} for file_id in attach_ids]})
            pauses = 0
            while True:
                if cancel_event and cancel_event.is_set():
                    raise DriverError("cancelled")
                async def create():
                    output_config = {"effort": map_effort("anthropic_api", effort)}
                    if response_schema is not None:
                        output_config["format"] = {"type": "json_schema", "schema": response_schema}
                    request_tools = list(specs)
                    if execution is not None:
                        request_tools.append({"type": execution.tool_version, "name": "code_execution"})
                    execution_system = system
                    if execution is not None:
                        execution_system += ("\nUse hosted code execution only for bounded analysis. If returning generated data files, "
                            "write only CSV or JSON files as analysis outputs; do not create or return executable content.")
                    options = {"model": model, "system": execution_system, "messages": rows,
                        "max_tokens": max_tokens, "tools": request_tools or None,
                        "output_config": output_config}
                    if container_id:
                        options["container"] = {"id": container_id}
                    return self.client.messages.stream(**options)
                reservation = None
                from django_core_micha.extraction.cost_guard import settle_reservation
                from .base import _reserve
                prompt_bytes = len(json.dumps(rows, ensure_ascii=False).encode()) + len(system.encode())
                reservation = _reserve(getattr(user, "pk", None), model, prompt_bytes, max_tokens)
                input_estimate, output_estimate = estimate_tokens(prompt_bytes, max_tokens)
                started = False
                buffered_text = []
                reported_requests = 0
                try:
                    if execution is not None and authorize and not await authorize():
                        raise DriverError("access_lost")
                    execution_possible = execution is not None
                    started = True
                    stream_context = await create()
                    async with stream_context as stream:
                        async for event in _stream_events(stream, execution):
                            kind = _get(event, "type", "")
                            if kind == "content_block_delta":
                                delta = _get(event, "delta")
                                if _get(delta, "type") == "text_delta":
                                    text = _get(delta, "text", "")
                                    if text and execution is None:
                                        yield {"type": "delta", "text": text}
                                    elif text:
                                        buffered_text.append(text)
                                        from ..limits import get_limit
                                        if sum(len(part.encode("utf-8")) for part in buffered_text) > get_limit("max_output_bytes"):
                                            raise DriverError("execution_output_invalid")
                                elif _get(delta, "type") == "input_json_delta":
                                    index = _get(event, "index")
                                    if index in execution_inputs:
                                        execution_inputs[index] += str(_get(delta, "partial_json", ""))
                                        from ..execution import _limits
                                        if len(execution_inputs[index].encode("utf-8")) > _limits()["max_execution_text_bytes"]:
                                            raise DriverError("execution_output_invalid")
                            elif kind == "content_block_start":
                                index = _get(event, "index")
                                block = _get(event, "content_block")
                                btype = _get(block, "type")
                                if btype == "server_tool_use":
                                    use_id = _get(block, "id")
                                    if not isinstance(use_id, str): raise DriverError("invalid_output")
                                    tool_name = _get(block, "name", "")
                                    execution_calls += 1
                                    if session:
                                        session.execution_calls = execution_calls
                                    if tool_name not in {"bash_code_execution", "text_editor_code_execution", "python", "code_execution"} or execution is None or execution_calls > execution.max_continuations:
                                        raise DriverError("execution_limit")
                                    execution_records.add(use_id)
                                    execution_possible = True
                                    if not isinstance(index, int): raise DriverError("invalid_output")
                                    execution_id_by_index[index] = use_id
                                    raw_input = _get(block, "input", {}) or {}
                                    execution_inputs[index] = json.dumps(raw_input, ensure_ascii=False) if raw_input else ""
                                    execution_code[use_id] = {"name": tool_name, "input": raw_input}
                                elif btype in {"bash_code_execution_tool_result", "code_execution_tool_result", "text_editor_code_execution_tool_result"}:
                                    use_id = _get(block, "tool_use_id")
                                    if use_id not in execution_records:
                                        raise DriverError("invalid_output")
                                    if use_id in completed_execution_records:
                                        continue
                                    completed_execution_records.add(use_id)
                                    result = _get(block, "content", {}) or {}
                                    stdout = _get(result, "stdout", "")
                                    stderr = _get(result, "stderr", "")
                                    return_code = _get(result, "return_code", 0)
                                    status = "completed" if return_code == 0 else "failed"
                                    file_ids = (_get(artifact, "file_id") for artifact in _blocks(_get(result, "content"))
                                        if _get(artifact, "type") in {"file", "file_reference"})
                                    data, file_truncation = await _download_files(
                                        self.client, file_ids, session.input_file_ids, session)
                                    provider_file_ids = list(session.provider_file_ids)
                                    from ..execution import _limits, bounded_text, bounded_text_parts, redact_provider_refs
                                    code_info = execution_code.get(use_id, {})
                                    input_value = code_info.get("input", {})
                                    code = _get(input_value, "command") or _get(input_value, "code") or ""
                                    code = str(code) if code else json.dumps(input_value, ensure_ascii=False, sort_keys=True)
                                    code, code_meta = bounded_text(code, _limits()["max_execution_text_bytes"])
                                    output_parts = [str(stdout)] + ([str(stderr)] if stderr else [])
                                    if not stdout and not stderr and isinstance(result, (str, dict)):
                                        output_parts = [result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)]
                                    output_text, output_meta = bounded_text_parts(
                                        output_parts, _limits()["max_execution_text_bytes"], separator="\n")
                                    record = {"code": code,
                                        "status": status, "output_text": output_text,
                                        "data": data}
                                    truncation = {"truncated": bool(file_truncation["truncated"] or
                                        code_meta["truncated"] or output_meta["truncated"]),
                                        "omitted_files": file_truncation["omitted_files"],
                                        "omitted_bytes": file_truncation["omitted_bytes"]}
                                    if code_meta["truncated"]:
                                        truncation["code"] = code_meta
                                    if output_meta["truncated"]:
                                        truncation["output_text"] = output_meta
                                    if truncation["truncated"]:
                                        record["truncation"] = truncation
                                    record = redact_provider_refs(record, [container_id, use_id, *provider_file_ids])
                                    yield {"type": "execution", "record": record}
                                    execution_possible = True
                            elif kind == "content_block_stop":
                                index = _get(event, "index")
                                if index in execution_inputs:
                                    try:
                                        execution_code_by_index = json.loads(execution_inputs[index] or "{}")
                                    except ValueError as exc:
                                        raise DriverError("invalid_output") from exc
                                    use_id = execution_id_by_index.get(index)
                                    if use_id:
                                        execution_code[use_id]["input"] = execution_code_by_index
                            elif kind == "message_start":
                                msg = _get(event, "message")
                                container = _get(msg, "container")
                                if container:
                                    value = _get(container, "id")
                                    if value:
                                        container_id = str(value)
                                        if session: session.container_id = container_id
                            elif kind == "message_delta":
                                pass
                        msg = await stream.get_final_message()
                        container = _get(msg, "container")
                        if container and _get(container, "id"):
                            container_id = str(_get(container, "id"))
                            if session: session.container_id = container_id
                    if execution is not None:
                        final_usage = _get(msg, "usage")
                        server_usage = _get(final_usage, "server_tool_use", {}) or {}
                        requests = _get(server_usage, "code_execution_requests", 0) or 0
                        reported_requests = max(0, int(requests))
                        execution_possible = execution_possible and bool(uploaded_ids or execution_calls or requests)
                        session.possible_charge = session.possible_charge or execution_possible
                        if buffered_text:
                            from ..execution import redact_provider_refs
                            yield {"type": "delta", "text": redact_provider_refs("".join(buffered_text),
                                [container_id, *provider_file_ids, *execution_records])}
                    settle_reservation(reservation=reservation, response=msg, model=model)
                except BaseException as exc:
                    if not started or _definitively_unbilled(exc):
                        release_unstarted(reservation)
                        if _definitively_unbilled(exc):
                            execution_possible = False
                    else:
                        if execution is not None:
                            execution_possible = True
                        await charge_aborted(reservation, user=user, provider="anthropic_api",
                            model=model, input_tokens_estimate=input_estimate,
                            output_tokens_estimate=output_estimate,
                            reference=billing_reference, purpose=billing_purpose,
                            source_id=billing_source_id, exc=exc)
                    raise
                usage = getattr(msg, "usage", None)
                from types import SimpleNamespace
                from django_core_micha.extraction.drivers._common import usage_from_response
                normalized_usage = usage_from_response(SimpleNamespace(usage=usage))
                bill_execution = execution is not None and (bool(uploaded_ids) or execution_possible)
                yield {"type": "usage", "usage": {"input_tokens": normalized_usage.input_tokens,
                    "output_tokens": normalized_usage.output_tokens,
                    "cache_read_tokens": normalized_usage.cache_read_tokens,
                    "cache_write_tokens": normalized_usage.cache_write_tokens,
                    **({"execution_usd_micros": execution.reserved_usd_micros if bill_execution and not execution_reported else 0,
                        "execution_requests": reported_requests} if execution is not None else {})},
                    "usage_reported": normalized_usage.reported}
                if bill_execution:
                    execution_reported = True
                    if session:
                        session.cost_reported = True
                content_blocks = list(getattr(msg, "content", []) or [])
                blocks = [b for b in content_blocks if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_use"]
                def plain(block):
                    if isinstance(block, dict): return block
                    if hasattr(block, "model_dump"): return block.model_dump(exclude_none=True)
                    return {key: getattr(block, key) for key in
                        ("type", "id", "name", "input", "text", "tool_use_id", "content", "is_error")
                        if hasattr(block, key)}
                if execution is not None and getattr(msg, "stop_reason", None) == "pause_turn":
                    pauses += 1
                    if blocks or pauses > execution.max_continuations or not container_id:
                        raise DriverError("security_violation")
                    rows.append({"role": "assistant", "content": [plain(block) for block in content_blocks]})
                    continue
                if not blocks:
                    return
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
                    refreshed_ids = []
                    if execution is not None:
                        refreshed_ids, execution = await _upload_execution_inputs(
                            self.client, execution, authorize, refresh_execution, cancel_event)
                        uploaded_ids = list(session.input_file_ids)
                        provider_file_ids = list(session.provider_file_ids)
                        if refreshed_ids:
                            execution_possible = True
                    result_blocks = [{"type": "tool_result", "tool_use_id": block_id, "content": content}]
                    result_blocks.extend({"type": "container_upload", "file_id": file_id}
                        for file_id in refreshed_ids)
                    rows.append({"role": "user", "content": result_blocks})
        except DriverError as exc:
            if session and execution_possible:
                session.possible_charge = True
            if execution_possible and not execution_reported:
                exc.execution_cost_estimate = execution.reserved_usd_micros
            raise
        except Exception as exc:
            if session and execution_possible:
                session.possible_charge = True
            if execution_possible and not execution_reported:
                exc.execution_cost_estimate = execution.reserved_usd_micros
            if execution is None:
                log_provider_error("anthropic_api", model, exc)
            else:
                log.warning("Hosted execution provider failed driver=anthropic_api type=%s", type(exc).__name__)
            code = "quota_exceeded" if getattr(exc, "status_code", None) == 429 else "provider_error"
            error = DriverError(code)
            if hasattr(exc, "execution_cost_estimate"):
                error.execution_cost_estimate = exc.execution_cost_estimate
            raise error from exc
        except BaseException as exc:
            if session and execution_possible:
                session.possible_charge = True
            if execution_possible and not execution_reported:
                try: exc.execution_cost_estimate = execution.reserved_usd_micros
                except Exception: pass
            raise
        finally:
            if session and execution_possible:
                session.possible_charge = True

    async def finish_execution(self, execution):
        """Finalize once, after all Assistant repair/continuation rounds finish."""
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
        for file_id in dict.fromkeys(session.provider_file_ids):
            try:
                async with asyncio.timeout(3):
                    await self.client.beta.files.delete(file_id, betas=["files-api-2025-04-14"])
            except Exception:
                log_provider_error("anthropic_api", "execution_cleanup", Exception("file cleanup failed"))
