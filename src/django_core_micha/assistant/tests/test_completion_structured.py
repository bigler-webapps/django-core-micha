from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings

pytestmark = pytest.mark.django_db

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}


@pytest.fixture(autouse=True)
def completion_catalog():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[
        {"driver": "openai_api", "model": "m", "efforts": ["low"], "billing": "pay_per_use", "default": True},
        {"driver": "anthropic_api", "model": "a", "efforts": ["low"], "billing": "pay_per_use"},
        {"driver": "claude_cli", "model": "c", "efforts": ["low"], "billing": "subscription"},
    ]):
        yield


@pytest.fixture
def completion_user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(username="structured-user", password="pw")


def _run(monkeypatch, user, driver, model, provider, **kwargs):
    from django_core_micha.assistant import completion
    monkeypatch.setattr(completion, "resolve_credential", lambda *args: ("key", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *args: provider)
    return async_to_sync(completion.acomplete)(user, prompt="original prompt", driver=driver, model=model, **kwargs)


def test_structured_completion_repairs_and_sums_usage(completion_user, monkeypatch):
    class Provider:
        def __init__(self): self.calls = []
        async def stream(self, **kwargs):
            self.calls.append(kwargs)
            answer = "{\"answer\": 3}" if len(self.calls) == 1 else "{\"answer\":\"ok\"}"
            yield {"type": "delta", "text": answer}
            yield {"type": "usage", "usage": {"input_tokens": len(self.calls), "output_tokens": 2,
                "cache_tokens": 1, "context_tokens": len(self.calls) * 10}}
    provider = Provider()
    result = _run(monkeypatch, completion_user, "openai_api", "m", provider, response_schema=SCHEMA)
    assert result.data == {"answer": "ok"}
    assert result.usage == {"input_tokens": 3, "output_tokens": 4, "cache_tokens": 2, "context_tokens": 20}
    assert "required JSON schema: $.answer: must be string." in provider.calls[1]["messages"][-1]["content"]
    assert provider.calls[1]["messages"][1] == {"role": "assistant", "content": "{\"answer\": 3}"}


def test_catalogue_entry_selecting_the_cli_repairs_through_the_cli_prompt(completion_user, monkeypatch):
    from django_core_micha.assistant import completion
    class Provider:
        def __init__(self): self.calls = []
        async def stream(self, **kwargs):
            self.calls.append(kwargs)
            yield {"type": "delta", "text": '{"answer": 3}' if len(self.calls) == 1 else '{"answer": "ok"}'}
    provider = Provider()
    monkeypatch.setattr(completion, "entry_by_id", lambda entry: {"driver": "claude_cli", "model": "c"})
    result = _run(monkeypatch, completion_user, None, None, provider, entry="cli-entry", response_schema=SCHEMA)
    import json
    assert result.data == {"answer": "ok"}
    assert "must be string" in json.loads(provider.calls[1]["prompt"])["message"]


def test_oversized_structured_frame_is_refused(completion_user, monkeypatch):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.base import DriverError
    class Provider:
        async def stream(self, **kwargs):
            yield {"type": "structured", "data": {"answer": "x" * 100}}
    limits = {"max_output_bytes": 50}
    real = completion.get_limit
    monkeypatch.setattr(completion, "get_limit", lambda name: limits.get(name) or real(name))
    with pytest.raises(DriverError) as error:
        _run(monkeypatch, completion_user, "claude_cli", "c", Provider(), response_schema=SCHEMA)
    assert error.value.code == "invalid_output"


def test_structured_frame_without_text_is_the_answer(completion_user, monkeypatch):
    class Provider:
        async def stream(self, **kwargs):
            yield {"type": "usage", "usage": {"input_tokens": 1, "output_tokens": 1}}
            yield {"type": "structured", "data": {"answer": "from-cli"}}
    result = _run(monkeypatch, completion_user, "claude_cli", "c", Provider(), response_schema=SCHEMA)
    assert result.data == {"answer": "from-cli"} and result.text == ""


@pytest.mark.parametrize("value,schema,valid", [
    ({"x": "b"}, {"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}], "enum": ["a"]}},
        "required": ["x"], "additionalProperties": False}, False),
    ({"x": True}, {"type": "object", "properties": {"x": {"type": "boolean", "enum": [1]}},
        "required": ["x"], "additionalProperties": False}, False),
    ({"x": 1}, {"type": "object", "properties": {"x": {"type": "integer", "const": 1}},
        "required": ["x"], "additionalProperties": False}, True),
    ({"x": float("nan")}, {"type": "object", "properties": {"x": {"type": "number"}},
        "required": ["x"], "additionalProperties": False}, False),
])
def test_validator_applies_every_constraint_with_json_equality(value, schema, valid):
    from django_core_micha.assistant.structured import validation_error
    assert (validation_error(value, schema) is None) is valid


def test_nan_in_the_answer_is_not_json(completion_user, monkeypatch):
    class Provider:
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            yield {"type": "delta", "text": '{"answer": NaN}' if self.calls == 1 else '{"answer": "ok"}'}
    provider = Provider()
    result = _run(monkeypatch, completion_user, "openai_api", "m", provider, response_schema=SCHEMA)
    assert result.data == {"answer": "ok"} and provider.calls == 2


def test_real_anthropic_driver_returns_validated_data(completion_user, monkeypatch):
    from anthropic.types import Message, TextBlock, Usage
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers import anthropic_api, base
    from django_core_micha.assistant.drivers.anthropic_api import AnthropicDriver

    class Messages:
        request = None
        def stream(self, **kwargs):
            Messages.request = kwargs
            class Context:
                @property
                def text_stream(self):
                    async def rows():
                        yield '{"answer": "ok"}'
                    return rows()
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return False
                async def get_final_message(self):
                    return Message(id="m1", type="message", role="assistant", model="a", stop_reason="end_turn",
                        stop_sequence=None, content=[TextBlock(type="text", text='{"answer": "ok"}')],
                        usage=Usage(input_tokens=3, output_tokens=2))
            return Context()

    monkeypatch.setattr(completion, "resolve_credential", lambda *args: ("key", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *args: AnthropicDriver(SimpleNamespace(messages=Messages())))
    monkeypatch.setattr(base, "_reserve", lambda *args: object())
    monkeypatch.setattr("django_core_micha.extraction.cost_guard.settle_reservation", lambda **kwargs: None)
    result = async_to_sync(completion.acomplete)(completion_user, prompt="p", driver="anthropic_api", model="a",
        response_schema=SCHEMA)
    assert result.data == {"answer": "ok"} and result.usage["input_tokens"] == 3
    assert Messages.request["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


def test_structured_completion_fails_after_maximum_repairs(completion_user, monkeypatch):
    class Provider:
        calls = 0
        async def stream(self, **kwargs):
            self.calls += 1
            yield {"type": "delta", "text": "not json"}
    provider = Provider()
    from django_core_micha.assistant.drivers.base import DriverError
    with pytest.raises(DriverError) as error:
        _run(monkeypatch, completion_user, "openai_api", "m", provider, response_schema=SCHEMA)
    assert error.value.code == "invalid_output" and provider.calls == 3


@pytest.mark.parametrize("schema", [
    {"type": ["object", "null"]},
    {"type": "object", "properties": {}, "required": []},
    {"type": "object", "properties": {"x": {"type": "string"}}, "required": [], "additionalProperties": False},
    {"type": "object", "properties": {"x": {"$ref": "#"}}, "required": ["x"], "additionalProperties": False},
    {"type": "string"},
    {"type": "object", "properties": {"x": {}}, "required": ["x"], "additionalProperties": False},
    {"type": "object", "properties": {"x": {"type": "string", "enum": []}}, "required": ["x"], "additionalProperties": False},
    {"type": "object", "properties": {"x": {"type": "string", "pattern": "^a"}}, "required": ["x"], "additionalProperties": False},
])
def test_invalid_schema_fails_before_resolving_credentials(completion_user, monkeypatch, schema):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers.base import DriverError
    resolver = Mock(side_effect=AssertionError("credential resolver called"))
    factory = Mock(side_effect=AssertionError("driver factory called"))
    monkeypatch.setattr(completion, "resolve_credential", resolver)
    monkeypatch.setattr(completion, "_make_driver", factory)
    with pytest.raises(DriverError) as error:
        async_to_sync(completion.acomplete)(completion_user, prompt="p", driver="openai_api", model="m", response_schema=schema)
    assert error.value.code == "invalid_schema"
    resolver.assert_not_called(); factory.assert_not_called()


def test_no_schema_keeps_result_shape(completion_user, monkeypatch):
    class Provider:
        async def stream(self, **kwargs):
            assert kwargs["response_schema"] is None
            yield {"type": "delta", "text": "ok"}
            yield {"type": "usage", "usage": {"input_tokens": 2}}
    result = _run(monkeypatch, completion_user, "openai_api", "m", Provider())
    assert result.text == "ok" and result.usage == {"input_tokens": 2} and result.data is None


def test_api_drivers_add_native_schema_settings():
    from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
    from django_core_micha.assistant.drivers.anthropic_api import AnthropicDriver
    from django_core_micha.assistant.drivers import openai_api

    class OpenResponses:
        def __init__(self): self.request = None
        async def create(self, **kwargs):
            self.request = kwargs
            async def events():
                from openai.types.responses import ResponseTextDeltaEvent
                from openai.types.responses import ResponseCompletedEvent
                # Provider SDK model instances keep the driver boundary realistic.
                yield ResponseTextDeltaEvent(type="response.output_text.delta", delta='{"answer":"ok"}', content_index=0, item_id="i", output_index=0, sequence_number=0, logprobs=[])
                yield ResponseCompletedEvent.model_construct(type="response.completed", response=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1, input_tokens_details=SimpleNamespace(cached_tokens=0)), output=[]))
            return events()

    class AnthropicMessages:
        def __init__(self): self.request = None
        def stream(self, **kwargs):
            self.request = kwargs
            class Context:
                @property
                def text_stream(self):
                    async def rows():
                        if False: yield ""
                    return rows()
                async def __aenter__(self): return self
                async def __aexit__(self, *args): return False
                async def get_final_message(self):
                    from anthropic.types import Message
                    return Message.model_construct(type="message", role="assistant", content=[], model="a",
                        stop_reason="end_turn", stop_sequence=None, id="id", usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
            return Context()

    async def run():
        open_rows = OpenResponses()
        monkey = pytest.MonkeyPatch()
        monkey.setattr(openai_api, "_reserve", lambda *args: object())
        monkey.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
        monkey.setattr(openai_api, "release_unstarted", lambda *args: None)
        monkey.setattr(openai_api, "charge_aborted", lambda *args, **kwargs: None)
        open_driver = OpenAIDriver(SimpleNamespace(responses=open_rows))
        async for _ in open_driver.stream(model="m", messages=[{"role": "user", "content": "p"}], response_schema=SCHEMA): pass
        assert open_rows.request["text"]["format"] == {"type": "json_schema", "name": "completion", "strict": True, "schema": SCHEMA}
        anth_rows = AnthropicMessages()
        anth_driver = AnthropicDriver(SimpleNamespace(messages=anth_rows))
        async for _ in anth_driver.stream(model="a", messages=[{"role": "user", "content": "p"}], response_schema=SCHEMA): pass
        assert anth_rows.request["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
        monkey.undo()
    async_to_sync(run)()


@pytest.mark.parametrize("deny_repair", [False, True])
def test_real_api_driver_guards_each_repair_call(completion_user, monkeypatch, deny_repair):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.drivers import openai_api
    from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
    from django_core_micha.assistant.drivers.base import DriverError

    class Responses:
        calls = 0
        formats = []
        async def create(self, **kwargs):
            self.calls += 1
            self.formats.append(kwargs.get("text", {}).get("format"))
            answer = '{"answer":3}' if self.calls == 1 else '{"answer":"ok"}'
            async def events():
                from openai.types.responses import ResponseTextDeltaEvent
                yield ResponseTextDeltaEvent(type="response.output_text.delta", delta=answer,
                    content_index=0, item_id="i", output_index=0, sequence_number=0, logprobs=[])
                from openai.types.responses import ResponseCompletedEvent
                yield ResponseCompletedEvent.model_construct(type="response.completed", response=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1, input_tokens_details=SimpleNamespace(cached_tokens=0)), output=[]))
            return events()

    responses = Responses()
    reserved, settled = [], []
    def reserve(*args):
        reserved.append(args)
        if deny_repair and len(reserved) == 2:
            raise DriverError("quota_exceeded")
        return object()
    monkeypatch.setattr(completion, "resolve_credential", lambda *args: ("key", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *args: OpenAIDriver(SimpleNamespace(responses=responses)))
    monkeypatch.setattr(openai_api, "_reserve", reserve)
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: settled.append(kwargs))
    monkeypatch.setattr(openai_api, "release_unstarted", lambda *args: None)
    monkeypatch.setattr(openai_api, "charge_aborted", lambda *args, **kwargs: None)
    if deny_repair:
        with pytest.raises(DriverError) as error:
            async_to_sync(completion.acomplete)(completion_user, prompt="p", driver="openai_api", model="m", response_schema=SCHEMA)
        assert error.value.code == "quota_exceeded"
        assert len(reserved) == 2 and len(settled) == 1
    else:
        result = async_to_sync(completion.acomplete)(completion_user, prompt="p", driver="openai_api", model="m", response_schema=SCHEMA)
        assert result.data == {"answer": "ok"}
        assert len(reserved) == len(settled) == 2
        native = {"type": "json_schema", "name": "completion", "strict": True, "schema": SCHEMA}
        assert responses.formats == [native, native]  # the repair call keeps the native schema


def test_cli_native_structured_output_and_init_tool_security(monkeypatch, tmp_path):
    from django_core_micha.assistant.drivers import claude_cli
    from django_core_micha.assistant.drivers.claude_cli import ClaudeCliDriver

    class Stdin:
        def __init__(self): self.data = b""
        def write(self, data): self.data += data
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass

    class Reader:
        def __init__(self, rows): self.rows = iter(rows)
        async def readline(self): return next(self.rows, b"")
        async def read(self): return b""

    class Proc:
        def __init__(self, rows):
            self.stdin, self.stdout, self.stderr = Stdin(), Reader(rows), Reader([])
            self.returncode, self.pid = None, 1
        async def wait(self): self.returncode = 0; return 0
        def kill(self): self.returncode = -9

    async def run(tools, schema=SCHEMA, supported=True, result=None):
        listed = tools
        rows = [
            {"type": "system", "subtype": "init", "tools": listed, "mcp_servers": []},
            result or {"type": "result", "usage": {}, "structured_output": {"answer": "ok"}, "result": '{"answer":"ok"}'},
        ]
        encoded = [json.dumps(row).encode() + b"\n" for row in rows]
        captured = {}
        async def factory(*argv, **kwargs):
            captured["argv"] = argv
            captured["proc"] = Proc(encoded)
            return captured["proc"]
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "claude-test")
        monkeypatch.setattr(claude_cli, "_supports_json_schema", lambda binary: supported)
        monkeypatch.setattr(claude_cli, "_session_dirs", lambda chat_id, create=False: (tmp_path, tmp_path, tmp_path))
        driver = ClaudeCliDriver(process_factory=factory, kill_tree=lambda proc: None)
        frames = [frame async for frame in driver.stream(prompt='{"system":"","message":"p"}', model="c",
            credential="oauth", chat=SimpleNamespace(pk="chat"), response_schema=schema)]
        return captured, frames

    import json
    captured, frames = async_to_sync(run)(["StructuredOutput"])
    argv = list(captured["argv"])
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert next(frame["data"] for frame in frames if frame["type"] == "structured") == {"answer": "ok"}
    for tool_list in ([], ["StructuredOutput", "unexpected"]):
        with pytest.raises(Exception) as error:
            async_to_sync(run)(tool_list)
        assert getattr(error.value, "code", None) == "security_violation"
    # without a schema the CLI's structured-output tool is never acceptable
    with pytest.raises(Exception) as error:
        async_to_sync(run)(["StructuredOutput"], schema=None)
    assert getattr(error.value, "code", None) == "security_violation"
    # an installed CLI without --json-schema: the schema travels in the prompt, no flag, no extra tool
    text_result = {"type": "result", "usage": {}, "result": '{"answer":"ok"}'}
    captured, frames = async_to_sync(run)([], supported=False, result=text_result)
    assert "--json-schema" not in captured["argv"]
    sent = json.loads(captured["proc"].stdin.data.decode())
    assert sent["message"].startswith("p\n\nAnswer with only a JSON object") and '"answer"' in sent["message"]
    assert not any(frame["type"] == "structured" for frame in frames)


def test_cli_json_schema_capability_probe_and_exact_argument(monkeypatch, tmp_path):
    import json
    import subprocess
    from django_core_micha.assistant.drivers import claude_cli

    def fake_run(stdout, code=0, raises=False):
        def run(argv, **kwargs):
            if raises: raise OSError("no binary")
            assert argv[1:] == ["--help"] and kwargs["timeout"] == 10
            return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")
        return run

    for name, run, expected in (
        ("with", fake_run("  --json-schema <schema>  JSON Schema for structured output"), True),
        ("without", fake_run("  --output-format <format>"), False),
        ("failing", fake_run("--json-schema", code=1), False),
        ("missing", fake_run("", raises=True), False),
    ):
        claude_cli._supports_json_schema.cache_clear()
        monkeypatch.setattr(subprocess, "run", run)  # the driver calls subprocess.run
        assert claude_cli._supports_json_schema(f"claude-{name}") is expected
    claude_cli._supports_json_schema.cache_clear()
