import re
from dataclasses import dataclass
import inspect
from django.db import transaction
from django.conf import settings
from django.utils.module_loading import import_string
@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    input_schema: dict
    handler: object

@dataclass(frozen=True)
class AfterTurnResult:
    text: str | None = None
    frames: list = ()
    repair: str | None = None

def _hook(name, default=None):
    value=getattr(settings, "DCM_ASSISTANT_"+name, default)
    return import_string(value) if isinstance(value,str) else value
def build_system_prompt(chat):
    fn=_hook("SYSTEM_PROMPT_BUILDER")
    return fn(chat) if fn else "You are a helpful assistant. Use registered read-only tools when useful."
def tool_registry():
    return _validate_tool_registry(_hook("TOOL_REGISTRY", []))

def _validate_tool_registry(value):
    if value is None:
        value = []
    # A hook that returns a scalar (False, 0, a string, a mapping) is a bug, not "no tools": fail the turn.
    if isinstance(value, (str, bytes, dict)) or not hasattr(value, "__iter__"):
        raise ValueError("invalid_tool_registry")
    try:
        tools = list(value)
        names = [tool.name for tool in tools]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid_tool_registry") from exc
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in names):
        raise ValueError("invalid_tool_registry")
    if len(names) != len(set(names)):
        raise ValueError("invalid_tool_registry")
    if any(not isinstance(getattr(tool, "input_schema", None), dict) for tool in tools):
        raise ValueError("invalid_tool_registry")
    return tools

def tools_for_chat(chat):
    hook = _hook("TOOLS_FOR_CHAT")
    tools = hook(chat) if hook else None
    if tools is None:
        tools = tool_registry() if hook is None or not chat.scope else []
    else:
        tools = _validate_tool_registry(tools)
    if _hook("GROUP_CATALOGUE") is None or is_shared_scope(chat.scope):
        return tools
    from .group_grants import allowed_tools
    allowed = allowed_tools(chat, chat.owner)
    return tools if allowed is None else [tool for tool in tools if tool.name in allowed]
def is_shared_scope(scope):
    if not scope:
        return False
    try:
        fn = _hook("SHARED_SCOPE")
        return bool(fn(scope)) if fn else False
    except Exception:
        return False

def validate_client_context(chat, user, context):
    fn = _hook("CLIENT_CONTEXT")
    return fn(chat, user, context) if fn else context

def can_access_scope(user, scope):
    fn=_hook("SCOPE_PERMISSION")
    return bool(fn(user, scope)) if fn else not scope

@transaction.atomic
def build_turn_context(chat, user, message, turn, client_context=None):
    fn = _hook("TURN_CONTEXT")
    if not fn: return None
    from .group_grants import allowed_selectors, authorized_chat
    if _hook("GROUP_CATALOGUE") is not None:
        # The live chat decides, never the caller's copy of its scope.
        chat = authorized_chat(getattr(chat, "pk", None), user)
        user = chat._authorized_actor
    selectors = allowed_selectors(chat, user)
    try:
        params = inspect.signature(fn).parameters.values()
        names = {p.name for p in params}
        variadic = any(p.kind == p.VAR_KEYWORD for p in params)
    except (TypeError, ValueError):
        names, variadic = set(), False
    # Legacy hooks cannot demonstrate that their data respects a private chat grant.
    if selectors is not None and "allowed_selectors" not in names:
        return None
    kwargs = {}
    if "client_context" in names or variadic:
        kwargs["client_context"] = client_context
    if selectors is not None:
        kwargs["allowed_selectors"] = frozenset(selectors)
    return fn(chat, user, message, turn, **kwargs)

def process_after_turn(chat, user, turn, text):
    fn = _hook("AFTER_TURN")
    return fn(chat, user, turn, text) if fn else None

def availability_reason(user, scope):
    fn = _hook("AVAILABILITY")
    return fn(user, scope) if fn else None

def turn_is_valid(chat, user, turn):
    fn = _hook("TURN_VALIDITY")
    return bool(fn(chat, user, turn)) if fn else True

def prompt_version():
    # Unlike callable hooks, this setting is a literal version tag; a string is not a dotted path.
    value = getattr(settings, "DCM_ASSISTANT_PROMPT_VERSION", None)
    return "" if value is None else str(value)
