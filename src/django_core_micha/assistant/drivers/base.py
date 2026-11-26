"""Shared provider errors, effort mapping, tool execution and spend accounting."""
import asyncio
import inspect
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from asgiref.sync import sync_to_async

log = logging.getLogger(__name__)
_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
DEFAULT_MAX_OUTPUT_TOKENS = 8192


def log_provider_error(driver, model, exc):
    """Log provider error details without allowing logging failures to mask the error."""
    try:
        body = getattr(exc, "body", None)
        fields = body if isinstance(body, dict) else {}
        nested = fields.get("error")
        if isinstance(nested, dict):
            fields = {**fields, **nested}
        message = fields.get("message") or (body if isinstance(body, str) else None) or getattr(exc, "message", None) or str(exc)
        error_type = fields.get("type") or getattr(exc, "type", None) or type(exc).__name__
        status = getattr(exc, "status_code", None)
        code = fields.get("code") or getattr(exc, "code", None)
        param = fields.get("param") or getattr(exc, "param", None)

        def clean(value):
            if value is None:
                return None
            return _API_KEY_RE.sub("[REDACTED]", str(value))[:1000]  # bounded: a provider message may quote request parts

        log.warning(
            "Provider call failed driver=%s model=%s status=%s type=%s code=%s param=%s message=%s",
            clean(driver), clean(model), clean(status), clean(error_type), clean(code), clean(param), clean(message),
        )
    except Exception:
        # Diagnostics must never replace the provider exception being handled.
        return


class DriverError(Exception):
    def __init__(self, code="provider_error", message=None):
        self.code = code
        self.before_init = False
        super().__init__(message or code)


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


def estimate_tokens(prompt_bytes, output_tokens=2048):
    """The token estimate a reservation prices (also what an aborted call is charged)."""
    return max(1, (prompt_bytes + 1) // 2), int(output_tokens)


def _reserve(user_id, model, prompt_bytes, output_tokens=2048):
    from django_core_micha.extraction.cost_guard import AICostLimitExceeded, estimate_cost_cents, reserve_user_budget
    input_tokens, output_tokens = estimate_tokens(prompt_bytes, output_tokens)
    try:
        return reserve_user_budget(user_id=user_id,
            cents=estimate_cost_cents(model=model, input_tokens=input_tokens, output_tokens=output_tokens))
    except AICostLimitExceeded as exc:
        raise DriverError("quota_exceeded") from exc


def release_unstarted(reservation):
    """Release a reservation for a provider call that was never started."""
    from django_core_micha.extraction.cost_guard import release_reservation
    release_reservation(reservation=reservation)


def _definitively_unbilled(exc):
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 429}


async def charge_aborted(reservation, *, user, provider, model, input_tokens_estimate,
                         output_tokens_estimate, reference, exc, source_id="", purpose="chat_aborted"):
    """Settle the local budget reservation and mark unreported provider usage unresolved."""
    from django_core_micha.extraction.cost_guard import settle_reservation
    try:
        settle_reservation(reservation=reservation, response=type("R", (), {"usage": Usage(
            int(input_tokens_estimate), int(output_tokens_estimate))})(), model=model)
    except Exception as settle_exc:
        # The provider failure being handled must stay the one that escapes.
        log.warning("Could not settle aborted AI call (%s)", type(settle_exc).__name__)
    try:
        from django.apps import apps
        if not apps.is_installed("django_core_micha.billing"):
            return
        from django_core_micha.billing.services import record_ai_call
        from asgiref.sync import sync_to_async
        await asyncio.shield(sync_to_async(record_ai_call, thread_sensitive=True)(
            user=user, provider=provider, model=model,
            input_tokens=0, output_tokens=0, source_id=source_id,
            usage_reported=False, purpose=purpose, reference=reference,
        ))
    except BaseException as record_exc:
        # Keep the provider exception (including cancellation) as the one that escapes.
        log.warning("Could not record aborted AI call (%s)", type(record_exc).__name__)


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


def _same_model_instance(left, right, *, authorized_chat=False):
    """Compare only fully loaded model state with safe, immutable field values."""
    from django.db.models import JSONField, Model

    if not isinstance(left, Model) or type(left) is not type(right):
        return False
    fields = left._meta.concrete_fields
    names = {field.attname for field in fields}
    extras = {"_state", "_authorized_actor"} if authorized_chat else {"_state"}
    if (set(left.__dict__) != names | extras
            or set(right.__dict__) != names | extras):
        return False
    ls, rs = left._state, right._state
    if (ls.adding or rs.adding or ls.db is None or ls.db != rs.db
            or left.pk is None or right.pk is None):
        return False
    if authorized_chat:
        from ..models import AssistantChat
        if type(left) is not AssistantChat:
            return False
        for chat, state in ((left, ls), (right, rs)):
            actor = chat.__dict__["_authorized_actor"]
            expected_cache = {"owner": actor} if chat.owner_id == getattr(actor, "pk", None) else {}
            if (state.fields_cache.keys() != expected_cache.keys()
                    or any(state.fields_cache[key] is not value for key, value in expected_cache.items())):
                return False
        if not _same_model_instance(left._authorized_actor, right._authorized_actor):
            return False
    elif ls.fields_cache or rs.fields_cache:
        return False
    safe_types = (str, bytes, int, float, bool, type(None), date, datetime, time, timedelta, Decimal, UUID)
    def same_json(a, b, depth=0):
        if depth > 32 or type(a) is not type(b):
            return False
        if type(a) in (str, int, float, bool, type(None)):
            return a == b
        if type(a) is list:
            return len(a) == len(b) and all(same_json(x, y, depth + 1) for x, y in zip(a, b))
        if type(a) is dict:
            return (all(type(key) is str for key in a) and a.keys() == b.keys()
                    and all(same_json(value, b[key], depth + 1) for key, value in a.items()))
        return False

    def same_field(field):
        a, b = left.__dict__[field.attname], right.__dict__[field.attname]
        if isinstance(field, JSONField):
            return same_json(a, b)
        return type(a) is type(b) and type(a) in safe_types and a == b

    return all(same_field(field) for field in fields)


def _same_safe_json(left, right):
    """Compare bounded JSON captures without accepting credentials or opaque objects."""
    remaining = [1000]
    sensitive = re.compile(r"(?:password|passwd|secret|token|credential|api[_-]?key|authorization)", re.I)

    def compare(a, b, depth):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 16 or type(a) is not type(b):
            return False
        if type(a) in (str, int, float, bool, type(None)):
            return a == b
        if type(a) is list:
            return len(a) == len(b) and all(compare(x, y, depth + 1) for x, y in zip(a, b))
        if type(a) is dict:
            return (len(a) == len(b) and all(type(k) is str and not sensitive.search(k) for k in a)
                    and a.keys() == b.keys()
                    and all(compare(v, b[k], depth + 1) for k, v in a.items()))
        return False

    return compare(left, right, 0)


def _same_handler(left, right):
    """Accept recreated methods/functions only when their captured bindings agree."""
    if left is right:
        return True
    if inspect.ismethod(left) and inspect.ismethod(right):
        return (left.__func__ is right.__func__ and
                (left.__self__ is right.__self__ or _same_model_instance(left.__self__, right.__self__)))
    if not (inspect.isfunction(left) and inspect.isfunction(right)):
        return False
    if left.__code__ is not right.__code__ or left.__globals__ is not right.__globals__:
        return False

    def same_binding(a, b):
        if a is b:
            return True
        return ((type(a) is type(b) and type(a) in (str, bytes, int, float, bool, type(None)) and a == b)
                or (type(a) in (list, dict) and _same_safe_json(a, b))
                or _same_model_instance(a, b)
                or _same_model_instance(a, b, authorized_chat=True))

    def same_values(a, b):
        return len(a) == len(b) and all(same_binding(x, y) for x, y in zip(a, b))

    try:
        return (same_values(left.__defaults__ or (), right.__defaults__ or ())
                and (left.__kwdefaults__ or {}).keys() == (right.__kwdefaults__ or {}).keys()
                and all(same_binding(value, right.__kwdefaults__[key])
                        for key, value in (left.__kwdefaults__ or {}).items())
                and same_values(tuple(cell.cell_contents for cell in left.__closure__ or ()),
                                tuple(cell.cell_contents for cell in right.__closure__ or ())))
    except ValueError:  # An empty closure cell cannot establish equivalence.
        return False


async def invoke_tool(tool, user, chat, arguments, trust_class):
    if not isinstance(arguments, dict):
        raise DriverError("invalid_output")
    try:
        from ..hooks import _hook
        from ..catalogue_activation import groups_enabled
        check_grants = await sync_to_async(groups_enabled)()
        handler = tool.handler
        if check_grants:
            from django.core.exceptions import PermissionDenied
            from ..group_grants import authorized_chat, tool_authorization
            try:
                chat = await sync_to_async(authorized_chat)(getattr(chat, "pk", None), user)
                user = chat._authorized_actor
            except PermissionDenied as exc:
                raise DriverError("access_lost") from exc
            allowed, token = await sync_to_async(tool_authorization)(chat.pk, user, tool.name)
            if not allowed:
                raise DriverError("access_lost")
            if token is not None:
                from ..hooks import tools_for_chat
                current = await sync_to_async(tools_for_chat)(chat)
                candidate = next((candidate for candidate in current if candidate.name == tool.name
                                  and _same_handler(candidate.handler, handler)), None)
                if candidate is None:
                    raise DriverError("access_lost")
                handler = candidate.handler
        if inspect.iscoroutinefunction(handler):
            result = await handler(user, chat, arguments, trust_class)
        else:
            result = await sync_to_async(handler)(user, chat, arguments, trust_class)
        if inspect.isawaitable(result):
            result = await result
        if check_grants:
            allowed, current_token = await sync_to_async(tool_authorization)(chat.pk, user, tool.name)
            if not allowed or current_token != token:
                raise DriverError("access_lost")
            if token is not None:
                current = await sync_to_async(tools_for_chat)(chat)
                if not any(candidate.name == tool.name and _same_handler(candidate.handler, handler) for candidate in current):
                    raise DriverError("access_lost")
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
