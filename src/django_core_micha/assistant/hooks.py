import re
from dataclasses import dataclass
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

def _hook(name, default=None):
    value=getattr(settings, "DCM_ASSISTANT_"+name, default)
    return import_string(value) if isinstance(value,str) else value
def build_system_prompt(chat):
    fn=_hook("SYSTEM_PROMPT_BUILDER")
    return fn(chat) if fn else "You are a helpful assistant. Use registered read-only tools when useful."
def tool_registry():
    tools = list((_hook("TOOL_REGISTRY", []) or []))
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)) or any(not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",name) for name in names):
        raise ValueError("invalid_tool_registry")
    if any(not isinstance(tool.input_schema,dict) for tool in tools):
        raise ValueError("invalid_tool_registry")
    return tools
def can_access_scope(user, scope):
    fn=_hook("SCOPE_PERMISSION")
    return bool(fn(user, scope)) if fn else not scope

def build_turn_context(chat, user, message, turn):
    fn = _hook("TURN_CONTEXT")
    return fn(chat, user, message, turn) if fn else None

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
