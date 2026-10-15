"""Persistent turn state machine shared by REST and WebSocket transports."""
import asyncio
import json
import logging
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .catalogue import validate
from .credentials import resolve_credential
from .drivers.base import DriverError
from .hooks import build_system_prompt, can_access_scope, tool_registry
from .history import build_history
from .limits import get_limit
from .models import AssistantChat, AssistantTurn

log = logging.getLogger(__name__)
TITLE_DEFAULT = AssistantChat._meta.get_field("title").default

def reconcile_interrupted(chat=None, everything=False):
    qs = AssistantTurn.objects.filter(state="generating")
    if chat is not None: qs = qs.filter(chat=chat)
    if not everything:
        qs = qs.filter(Q(heartbeat_at__isnull=True) | Q(heartbeat_at__lt=timezone.now()-timedelta(seconds=get_limit("stale_turn_seconds"))))
    return qs.update(state="interrupted", error_code="interrupted", finished_at=timezone.now())

def _beat(turn_id):
    AssistantTurn.objects.filter(pk=turn_id, state="generating").update(heartbeat_at=timezone.now())

def _over_limit(turn, user_id):
    """Re-count after commit: concurrent starts cannot see each other's uncommitted rows."""
    fresh = timezone.now() - timedelta(seconds=get_limit("stale_turn_seconds"))
    active = AssistantTurn.objects.filter(state="generating", heartbeat_at__gte=fresh).order_by("created_at", "pk")
    ids = list(active.values_list("pk", flat=True))
    mine = list(active.filter(chat__owner_id=user_id).values_list("pk", flat=True))
    return (turn.pk in ids and ids.index(turn.pk) >= get_limit("max_concurrent_turns")) or         (turn.pk in mine and mine.index(turn.pk) >= get_limit("max_turns_per_user"))

def _start(chat_id, user_id, message):
    turn = _create_turn(chat_id, user_id, message)
    if _over_limit(turn, user_id):
        turn.delete()
        raise DriverError("rate_limited")
    return turn

def _create_turn(chat_id, user_id, message):
    reconcile_interrupted()
    try:
        with transaction.atomic():
            chat = AssistantChat.objects.select_for_update().get(pk=chat_id, owner_id=user_id)
            if chat.archived_at: raise DriverError("archived")
            if not chat.selected_driver or not chat.selected_model: raise DriverError("invalid_selection")
            validate(chat.selected_driver, chat.selected_model, chat.selected_effort)
            if chat.turns.filter(state="generating").exists(): raise DriverError("turn_active")
            active = AssistantTurn.objects.filter(state="generating", heartbeat_at__gte=timezone.now()-timedelta(seconds=get_limit("stale_turn_seconds")))
            if active.count() >= get_limit("max_concurrent_turns") or active.filter(chat__owner_id=user_id).count() >= get_limit("max_turns_per_user"):
                raise DriverError("rate_limited")
            seq = (chat.turns.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1
            return AssistantTurn.objects.create(chat=chat, sequence=seq, user_text=message,
                driver=chat.selected_driver, model=chat.selected_model, effort=chat.selected_effort,
                heartbeat_at=timezone.now())
    except IntegrityError as exc:
        raise DriverError("turn_active") from exc

def _finish(turn_id, text, state, error="", usage=None, trace=None, session_id=None):
    with transaction.atomic():
        turn = AssistantTurn.objects.select_for_update().select_related("chat").get(pk=turn_id)
        if turn.state != "generating": return False
        turn.assistant_text, turn.state, turn.error_code = text, state, error
        turn.finished_at, turn.usage, turn.tool_trace = timezone.now(), usage or {}, trace or []
        turn.save(update_fields=("assistant_text", "state", "error_code", "finished_at", "usage", "tool_trace"))
        chat = turn.chat
        if state == "completed" and turn.sequence == 1 and chat.title_source == "automatic" and chat.title == TITLE_DEFAULT:
            title = re.sub(r"\s+", " ", turn.user_text).strip()
            if len(title) > 60:
                title = title[:60].rsplit(" ", 1)[0] if " " in title[:60] else title[:60]
            chat.title = title[:60] or TITLE_DEFAULT
        if session_id and turn.driver == "claude_cli":
            chat.session_driver, chat.session_reference = turn.driver, session_id
        elif turn.driver != "claude_cli":
            chat.session_driver, chat.session_reference = "", ""
        chat.updated_at = timezone.now()
        chat.save(update_fields=("title", "session_driver", "session_reference", "updated_at"))
        return True

async def _heartbeat(turn_id):
    while True:
        await asyncio.sleep(get_limit("heartbeat_seconds"))
        await sync_to_async(_beat)(turn_id)

def _make_driver(name, credential):
    if name == "claude_cli":
        from .drivers.claude_cli import ClaudeCliDriver
        return ClaudeCliDriver()
    if name == "openai_api":
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise DriverError("not_configured", "Install django-core-micha[ai] to use AI providers.") from exc
        from .drivers.openai_api import OpenAIDriver
        return OpenAIDriver(AsyncOpenAI(api_key=credential))
    if name == "anthropic_api":
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise DriverError("not_configured", "Install django-core-micha[ai] to use AI providers.") from exc
        from .drivers.anthropic_api import AnthropicDriver
        return AnthropicDriver(AsyncAnthropic(api_key=credential))
    raise DriverError("not_configured")

async def run_turn(chat, user, message, emit, cancel_event=None):
    if not isinstance(message, str) or not message.strip(): raise DriverError("invalid_output")
    if len(message) > get_limit("max_message_length"): raise DriverError("prompt_too_long")
    turn = await sync_to_async(_start)(chat.pk, user.pk, message)
    beat = asyncio.create_task(_heartbeat(turn.pk))
    chunks, trace, usage, session_id = [], [], {}, None
    session_file = False
    saw_init = False
    try:
        # Resolver is deliberately the one credential seam and runs exactly once per turn.
        credential, billing = resolve_credential(user, turn.driver)
        await sync_to_async(AssistantTurn.objects.filter(pk=turn.pk).update)(billing_source=billing)
        if turn.driver != "claude_cli" and not credential:
            raise DriverError("not_configured")
        current = await sync_to_async(AssistantChat.objects.get)(pk=chat.pk)
        prompt = await sync_to_async(build_system_prompt)(current)
        history = await sync_to_async(build_history)(current)
        tools = await sync_to_async(tool_registry)()
        if turn.driver == "claude_cli" and current.session_driver == turn.driver and current.session_reference:
            from .drivers.claude_cli import _session_dirs
            _, config_dir, _ = _session_dirs(current.pk)
            session_file = any((config_dir / "projects").glob(f"*/{current.session_reference}.jsonl"))
        prompt_data = json.dumps({"system": prompt, "message": message} if session_file else
            {"system": prompt, "history": history, "message": message}, ensure_ascii=False)
        if len(prompt_data.encode("utf-8")) > get_limit("max_prompt_bytes"):
            raise DriverError("prompt_too_long")
        if turn.driver == "claude_cli" and not session_file and current.session_reference:
            await emit({"type":"notice","code":"history_rebuilt"})
        # A provider switch or missing CLI session rebuilds history from durable turns.
        if turn.driver == "claude_cli" and (current.session_driver != turn.driver or not current.session_reference):
            history_rows = history
        else: history_rows = history
        driver = _make_driver(turn.driver, credential)
        kwargs = {"prompt": prompt_data, "messages": history_rows + [{"role": "user", "content": message}],
            "system": prompt, "tools": tools, "user": user, "chat": current, "credential": credential,
            "effort": turn.effort, "cancel_event": cancel_event,
            "session_id": current.session_reference if session_file else None,
            "max_tool_calls": get_limit("max_tool_calls")}
        async with asyncio.timeout(get_limit("provider_timeout")):
            async for event in driver.stream(model=turn.model, **kwargs):
                if cancel_event and cancel_event.is_set(): raise asyncio.CancelledError
                if event.get("type") == "delta":
                    chunks.append(event.get("text", ""))
                    if sum(len(chunk.encode("utf-8")) for chunk in chunks) > get_limit("max_output_bytes"): raise DriverError("invalid_output")
                    await emit({"type": "delta", "text": event["text"]})
                elif event.get("type") == "tool":
                    if event.get("phase") == "end": trace.append({"name": event["name"], "label": event["label"], "sources": event.get("sources", [])})
                    await emit(event)
                elif event.get("type") == "usage": usage = event.get("usage", {})
                elif event.get("type") == "session": session_id = event.get("session_id")
                elif event.get("type") == "init": saw_init = True
        text = "".join(chunks)
        await sync_to_async(_finish)(turn.pk, text, "completed", usage=usage, trace=trace, session_id=session_id)
    except asyncio.CancelledError as exc:
        if getattr(exc, "before_init", False) and session_file:
            await sync_to_async(AssistantChat.objects.filter(pk=chat.pk).update)(session_driver="", session_reference="")
        await sync_to_async(_finish)(turn.pk, "".join(chunks), "cancelled", "cancelled", usage, trace)
        try:
            cancelled = await sync_to_async(AssistantTurn.objects.get)(pk=turn.pk)
            await emit({"type":"turn","turn":turn_payload(cancelled)})
        except Exception:
            pass
        raise
    except TimeoutError as exc:
        if session_file and not saw_init:
            await sync_to_async(AssistantChat.objects.filter(pk=chat.pk).update)(session_driver="", session_reference="")
        await sync_to_async(_finish)(turn.pk, "".join(chunks), "failed", "timeout", usage, trace)
        await _emit_final(turn.pk, emit)
        raise DriverError("timeout") from exc
    except Exception as exc:
        code = getattr(exc, "code", "provider_error")
        if isinstance(exc, DriverError) and getattr(exc, "before_init", False) and session_file:
            await sync_to_async(AssistantChat.objects.filter(pk=chat.pk).update)(session_driver="", session_reference="")
        await sync_to_async(_finish)(turn.pk, "".join(chunks), "failed", code, usage, trace)
        await _emit_final(turn.pk, emit)
        if isinstance(exc, DriverError): raise
        log.exception("Assistant provider turn failed")
        raise DriverError(code) from exc
    finally:
        beat.cancel()
        try: await beat
        except BaseException: pass
    final = await sync_to_async(AssistantTurn.objects.get)(pk=turn.pk)
    payload = turn_payload(final)
    await emit({"type": "turn", "turn": payload})
    return final

async def _emit_final(turn_id, emit):
    try:
        row = await sync_to_async(AssistantTurn.objects.get)(pk=turn_id)
        await emit({"type": "turn", "turn": turn_payload(row)})
    except Exception:
        log.exception("Could not emit the final turn frame")

def turn_payload(turn):
    return {"id": str(turn.pk), "seq": turn.sequence, "user_text": turn.user_text,
        "assistant_text": turn.assistant_text, "state": turn.state, "error_code": turn.error_code or None,
        "sources": [source for item in turn.tool_trace for source in item.get("sources", [])],
        "driver": turn.driver, "model": turn.model, "effort": turn.effort,
        "usage": turn.usage or {}, "billing_source": turn.billing_source}

def delete_session(chat):
    root = Path(getattr(settings, "DCM_ASSISTANT_CLI_ROOT", Path(tempfile.gettempdir()) / "dcm-assistant")).resolve()
    target = root / str(chat.pk)
    if target.parent == root and not target.is_symlink(): shutil.rmtree(target, ignore_errors=True)
