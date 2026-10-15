import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def catalogue():
    rows = [{"driver": "openai_api", "model": "gpt-test", "name": "test", "efforts": ["low", "medium"],
             "billing": "pay_per_use", "default": True}]
    with override_settings(DCM_ASSISTANT_CATALOGUE=rows, DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: True):
        yield


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="tools-user", password="secret")


def _tool(name, handler=None, schema=None):
    return NS(name=name, label=name.title(), description=f"{name} description",
              input_schema={"type": "object"} if schema is None else schema,
              handler=handler or (lambda *_: ("ok", [])))


def _chat(user, scope=""):
    return AssistantChat.objects.create(owner=user, selected_driver="openai_api", selected_model="gpt-test", scope=scope)


def _run(monkeypatch, chat, user, driver, frames):
    from django_core_micha.assistant import turns

    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "build_turn_context", lambda *_: None)
    monkeypatch.setattr(turns, "can_access_scope", lambda user, scope: True)
    monkeypatch.setattr(turns, "_make_driver", lambda *_: driver)

    async def emit(frame):
        frames.append(frame)

    async def run():
        return await turns.run_turn(chat, user, "question", emit)

    return async_to_sync(run)


class CaptureDriver:
    def __init__(self):
        self.offered = []

    async def stream(self, **kwargs):
        self.offered.append((kwargs["chat"].scope, kwargs["tools"]))
        yield {"type": "delta", "text": "ok"}


def test_scope_hook_offers_only_its_tools_and_isolated_sets(monkeypatch, user):
    global_tool, alpha_tool, beta_tool = _tool("global"), _tool("alpha"), _tool("beta")
    alpha, beta = _chat(user, "pr:alpha"), _chat(user, "pr:beta")
    hook_calls = []

    def hook(chat):
        hook_calls.append(chat.scope)
        return {"pr:alpha": [alpha_tool], "pr:beta": [beta_tool]}[chat.scope]

    driver = CaptureDriver()
    with override_settings(DCM_ASSISTANT_TOOL_REGISTRY=[global_tool], DCM_ASSISTANT_TOOLS_FOR_CHAT=hook):
        _run(monkeypatch, alpha, user, driver, [])()
        _run(monkeypatch, beta, user, driver, [])()

    assert hook_calls == ["pr:alpha", "pr:beta"]
    assert driver.offered == [("pr:alpha", [alpha_tool]), ("pr:beta", [beta_tool])]
    assert global_tool not in driver.offered[0][1] and beta_tool not in driver.offered[0][1]
    assert alpha_tool not in driver.offered[1][1] and global_tool not in driver.offered[1][1]


def test_none_uses_global_only_for_unscoped_chat(monkeypatch, user):
    global_tool = _tool("global")
    unscoped, scoped = _chat(user), _chat(user, "pr:alpha")
    driver = CaptureDriver()
    with override_settings(DCM_ASSISTANT_TOOL_REGISTRY=[global_tool], DCM_ASSISTANT_TOOLS_FOR_CHAT=lambda chat: None):
        _run(monkeypatch, unscoped, user, driver, [])()
        _run(monkeypatch, scoped, user, driver, [])()
    assert driver.offered == [("", [global_tool]), ("pr:alpha", [])]


@pytest.mark.parametrize("scope", ["", "pr:alpha"])
def test_empty_hook_result_offers_no_tools(monkeypatch, user, scope):
    global_tool = _tool("global")
    chat = _chat(user, scope)
    driver = CaptureDriver()
    with override_settings(DCM_ASSISTANT_TOOL_REGISTRY=[global_tool], DCM_ASSISTANT_TOOLS_FOR_CHAT=lambda current: []):
        _run(monkeypatch, chat, user, driver, [])()
    assert driver.offered == [(scope, [])]


def test_unset_hook_keeps_global_tools_for_scoped_chat(monkeypatch, user):
    global_tool = _tool("global")
    chat = _chat(user, "pr:alpha")
    driver = CaptureDriver()
    with override_settings(DCM_ASSISTANT_TOOL_REGISTRY=[global_tool]):
        _run(monkeypatch, chat, user, driver, [])()
    assert driver.offered == [("pr:alpha", [global_tool])]


@pytest.mark.parametrize("invalid", [
    [_tool("duplicate"), _tool("duplicate")],
    [_tool("not valid")],
    [_tool("schema", schema=[])],
    False,
    0,
    "alpha",
])
def test_invalid_hook_tools_fail_turn_before_provider(monkeypatch, user, invalid):
    chat, called = _chat(user, "pr:alpha"), []
    class Driver:
        async def stream(self, **kwargs):
            called.append(True)
            yield {"type": "delta", "text": "unexpected"}

    with override_settings(DCM_ASSISTANT_TOOLS_FOR_CHAT=lambda current: invalid):
        with pytest.raises(DriverError) as error:
            _run(monkeypatch, chat, user, Driver(), [])()
    turn = AssistantTurn.objects.get(chat=chat)
    assert error.value.code == "provider_error"
    assert called == [] and turn.state == "failed" and turn.error_code == "provider_error"


def test_raising_hook_fails_turn_before_provider(monkeypatch, user):
    chat, called = _chat(user, "pr:alpha"), []
    class Driver:
        async def stream(self, **kwargs):
            called.append(True)
            yield {"type": "delta", "text": "unexpected"}

    def fail(current):
        raise RuntimeError("hook failure")

    with override_settings(DCM_ASSISTANT_TOOLS_FOR_CHAT=fail):
        with pytest.raises(DriverError) as error:
            _run(monkeypatch, chat, user, Driver(), [])()
    turn = AssistantTurn.objects.get(chat=chat)
    assert error.value.code == "provider_error"
    assert called == [] and turn.state == "failed" and turn.error_code == "provider_error"


def test_tool_handler_receives_chat_scope(user):
    from django_core_micha.assistant.drivers.base import invoke_tool

    chat, seen = _chat(user, "pr:alpha"), []

    def handler(current_user, current_chat, arguments, trust_class):
        seen.append((current_chat, current_chat.scope, arguments, trust_class))
        return "ok", []

    assert async_to_sync(invoke_tool)(_tool("scoped", handler), user, chat, {"q": "x"}, "cloud") == ("ok", [])
    assert seen == [(chat, "pr:alpha", {"q": "x"}, "cloud")]


def test_openai_refuses_tool_outside_chat_set(monkeypatch, user):
    from django_core_micha.assistant.drivers import openai_api

    handler_calls = []
    outside = _tool("global", lambda *_: (handler_calls.append(True) or "bad", []))
    class Responses:
        async def create(self, **kwargs):
            async def stream():
                yield NS(type="response.completed", response=NS(usage=NS(input_tokens=1, output_tokens=0), output=[
                    NS(type="function_call", id="item", call_id="call", name="global", arguments="{}")]))
            return stream()

    monkeypatch.setattr(openai_api, "_reserve", lambda *args: object())
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(openai_api, "release_reservation", lambda **kwargs: None)
    driver = openai_api.OpenAIDriver(NS(responses=Responses()))

    async def run():
        return [event async for event in driver.stream(model="gpt-test", messages=[], tools=[_tool("alpha", lambda *_: ("fine", []))], user=user)]

    with pytest.raises(DriverError) as error:
        async_to_sync(run)()
    assert error.value.code == "security_violation" and handler_calls == []


def test_anthropic_refuses_tool_outside_chat_set(monkeypatch, user):
    from django_core_micha.assistant.drivers import base, anthropic_api
    from django_core_micha.extraction import cost_guard

    handler_calls = []
    class Stream:
        @property
        def text_stream(self):
            async def empty():
                if False: yield ""
            return empty()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get_final_message(self):
            return NS(usage=NS(input_tokens=1, output_tokens=0), content=[NS(type="tool_use", id="id", name="other_scope", input={})])

    client = NS(messages=NS(stream=lambda **kwargs: Stream()))
    monkeypatch.setattr(base, "_reserve", lambda *args: object())
    monkeypatch.setattr(cost_guard, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    driver = anthropic_api.AnthropicDriver(client)

    async def run():
        return [event async for event in driver.stream(model="claude-test", messages=[], tools=[_tool("alpha", lambda *_: ("fine", []))], user=user)]

    with pytest.raises(DriverError) as error:
        async_to_sync(run)()
    assert error.value.code == "security_violation" and handler_calls == []


def test_claude_bridge_lists_only_chat_tools_and_refuses_other_scope(monkeypatch, tmp_path):
    from django_core_micha.assistant import cli_bridge

    config_path = tmp_path / "bridge.json"
    config_path.write_text(json.dumps({"endpoint": "tcp:127.0.0.1:1", "nonce": "n", "tools": [
        {"name": "alpha", "description": "alpha", "inputSchema": {"type": "object"}},
    ]}), encoding="utf-8")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "beta", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "alpha", "arguments": {}}},
    ]
    reached = []
    output = []
    monkeypatch.setenv("DCM_ASSISTANT_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setattr(cli_bridge.sys, "stdin", [json.dumps(row) for row in requests])
    monkeypatch.setattr(cli_bridge.sys, "stdout", NS(write=lambda text: output.append(text), flush=lambda: None))
    monkeypatch.setattr(cli_bridge, "_call", lambda name, arguments: reached.append(name) or {"status": "ok", "content": "done"})

    cli_bridge.main()
    rows = [json.loads(line) for line in output]
    assert rows[0]["result"]["tools"] == [{"name": "alpha", "description": "alpha", "inputSchema": {"type": "object"}}]
    assert rows[1]["result"]["isError"] is True
    assert rows[1]["result"]["content"][0]["text"] == "tool_not_allowed"
    assert reached == ["alpha"] and rows[2]["result"]["isError"] is False  # the offered tool still works


def test_claude_init_event_with_extra_tool_is_refused(monkeypatch, tmp_path):
    from django_core_micha.assistant.drivers.claude_cli import ClaudeCliDriver

    monkeypatch.setattr("django_core_micha.assistant.drivers.claude_cli.shutil.which", lambda name: "claude")
    proc = _cli_proc([b'{"type":"system","subtype":"init","tools":["mcp__dcm_tools__alpha","mcp__dcm_tools__beta"],'
                      b'"mcp_servers":[{"name":"dcm_tools","status":"connected"}]}\n'])
    async def factory(*args, **kwargs): return proc
    driver = ClaudeCliDriver(process_factory=factory, kill_tree=lambda process: None)
    offered = _tool("alpha")

    async def run():
        return [event async for event in driver.stream(prompt="p", model="m", tools=[offered], credential="x", chat=NS(pk="init-extra"))]

    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as error:
            async_to_sync(run)()
    assert error.value.code == "security_violation"


def _cli_proc(rows):
    class Input:
        def write(self, data): pass
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    class Output:
        def __init__(self): self.lines = iter(rows)
        async def readline(self): return next(self.lines, b"")
    class Proc:
        pid = 1
        returncode = None
        def __init__(self):
            self.stdin, self.stdout = Input(), Output()
            self.stderr = NS(read=AsyncMock(return_value=b""))
        async def wait(self): self.returncode = 0; return 0
    return Proc()
