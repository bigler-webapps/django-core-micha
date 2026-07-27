"""Code-first registry for provider-derived todo types."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class TodoSeed:
    """One provider-emitted candidate todo before visibility/override resolution."""

    type_key: str
    recipient: Any
    content: dict
    notifiable: Any | None = None
    scope: Any | None = None
    due_base_resolver: Callable[[str], Any] | None = None
    has_due_time: bool = False


@dataclass(frozen=True)
class TodoTypeConfig:
    """Materialization configuration for one registered todo type."""

    type_key: str
    due: str | None = None
    remind_before: str | None = None
    severity: str | None = None
    persist_until_done: bool = False
    always_visible: bool = False
    lead_adjustable: bool = False


TodoProviderFn = Callable[[Any, datetime], Iterable[TodoSeed]]
CandidateUsersFn = Callable[[datetime], Iterable[Any]]

_PROVIDERS: dict[str, TodoProviderFn] = {}
_CONFIGS: dict[str, TodoTypeConfig] = {}
_CANDIDATE_USERS: dict[str, CandidateUsersFn] = {}


def register_todo_provider(type_key: str, provider_fn: TodoProviderFn, *, config: TodoTypeConfig, candidate_users_fn: CandidateUsersFn | None = None) -> None:
    """Register or replace a type's provider and configuration."""

    if config.type_key != type_key:
        raise ValueError("TodoTypeConfig.type_key must match the registered type key")
    _PROVIDERS[type_key] = provider_fn
    _CONFIGS[type_key] = config
    if candidate_users_fn is None:
        _CANDIDATE_USERS.pop(type_key, None)
    else:
        _CANDIDATE_USERS[type_key] = candidate_users_fn


def get_todo_provider(type_key: str) -> TodoProviderFn:
    try:
        return _PROVIDERS[type_key]
    except KeyError as exc:
        raise LookupError(f"Unknown todo provider: {type_key}") from exc


def get_todo_config(type_key: str) -> TodoTypeConfig:
    try:
        return _CONFIGS[type_key]
    except KeyError as exc:
        raise LookupError(f"Unknown todo type: {type_key}") from exc


def iter_registered_todo_types() -> Iterable[str]:
    return tuple(_PROVIDERS)


def iter_candidate_users_fns() -> Iterable[CandidateUsersFn]:
    return tuple(_CANDIDATE_USERS.values())
