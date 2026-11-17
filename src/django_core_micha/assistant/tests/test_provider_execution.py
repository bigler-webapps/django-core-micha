import asyncio
import copy
import json
from types import SimpleNamespace as NS

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.execution import (
    ANTHROPIC_RETENTION_POLICY, Dataset, ExecutionConfig, model_supports_execution, prepare_execution,
    validate_outputs,
)
from django_core_micha.assistant.hooks import ToolSpec
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
from django_core_micha.assistant.turns import _finish, turn_payload


@pytest.fixture(autouse=True)
def verified_synthetic_models(monkeypatch):
    from django_core_micha.assistant import execution
    monkeypatch.setattr(execution, "VERIFIED_EXECUTION_MODELS", {
        "openai_api": {"synthetic-model"}, "anthropic_api": {"synthetic-model"}})


def _entry(driver="openai_api", model="synthetic-model", version="code_interpreter"):
    return {"driver": driver, "model": model, "code_execution": {
        "enabled": True, "tool_version": version, "memory_limit": "1g",
        "max_execution_seconds": 90, "max_continuations": 3}}


def _config(driver="openai_api", datasets=None):
    return ExecutionConfig(driver, "synthetic-model",
        "code_interpreter" if driver == "openai_api" else "code_execution_20250825",
        "1g", 90, 3, 30_000,
        tuple(Dataset(**row) for row in ([_dataset()] if datasets is None else datasets)))


def _dataset():
    return {"name": "rows.csv", "data_class": "synthetic", "content": b"a,b\n1,2\n", "media_type": "text/csv"}


@pytest.mark.parametrize("driver,version", [
    ("openai_api", "code_interpreter"),
    ("anthropic_api", "code_execution_20250825"),
])
def test_execution_policy_defaults_deny_and_anthropic_requires_app_and_each_class(driver, version):
    user = NS(pk=3)
    chat = NS(pk=10, owner_id=3, scope="")
    turn = NS(driver=driver, model="synthetic-model")
    entry = _entry(driver, version=version)
    pricing = {f"{driver}:synthetic-model": {"usd_micros": 30_000,
        "unit": "openai_container_20m" if driver == "openai_api" else "anthropic_execution_5m"}}
    async def authorize(): return True
    async def run(policy):
        with override_settings(DCM_ASSISTANT_EXECUTION_POLICY=policy,
            DCM_ASSISTANT_EXECUTION_PRICING=pricing):
            return await prepare_execution(chat, user, turn, entry, authorize=authorize)

    assert async_to_sync(run)(None) is None
    approved = {"enabled": True, "datasets": [_dataset()]}
    if driver == "anthropic_api":
        approved["anthropic_retention_approval"] = {
            "policy": ANTHROPIC_RETENTION_POLICY, "app": True, "data_classes": ["synthetic"]}
        assert async_to_sync(run)(lambda *_: {**approved, "anthropic_retention_approval": {
            "policy": ANTHROPIC_RETENTION_POLICY, "app": False, "data_classes": ["synthetic"]}}) is None
        assert async_to_sync(run)(lambda *_: {**approved, "anthropic_retention_approval": {
            "policy": ANTHROPIC_RETENTION_POLICY, "app": True, "data_classes": []}}) is None
    prepared = async_to_sync(run)(lambda *_: approved)
    assert prepared is not None and [row.name for row in prepared.datasets] == ["rows.csv"]


def test_policy_rejects_shared_revoked_foreign_and_unpriced_inputs_before_upload():
    user = NS(pk=3)
    turn = NS(driver="openai_api", model="synthetic-model")
    entry = _entry()
    pricing = {"openai_api:synthetic-model": {"usd_micros": 30_000, "unit": "openai_container_20m"}}
    calls = []
    def policy(*_):
        calls.append(True)
        return {"enabled": True, "datasets": [_dataset()]}
    async def yes(): return True
    async def no(): return False

    async def prepare(chat, auth=yes, price=pricing, hook=policy):
        with override_settings(DCM_ASSISTANT_EXECUTION_POLICY=hook,
                               DCM_ASSISTANT_EXECUTION_PRICING=price,
                               DCM_ASSISTANT_SHARED_SCOPE=lambda scope: scope == "shared"):
            return await prepare_execution(chat, user, turn, entry, authorize=auth)

    assert async_to_sync(prepare)(NS(pk=1, owner_id=3, scope="shared")) is None
    assert async_to_sync(prepare)(NS(pk=1, owner_id=3, scope=""), no) is None
    assert calls == []
    assert async_to_sync(prepare)(NS(pk=1, owner_id=3, scope=""), price={}) is None
    # Invalid trusted-hook output is a denial before provider contact, not an implicit upload.
    with pytest.raises(DriverError):
        async_to_sync(prepare)(NS(pk=1, owner_id=3, scope=""), hook=lambda *_: {
            "enabled": True, "datasets": [{**_dataset(), "provider_file_id": "foreign-id"}]})


def test_same_chat_follow_up_receives_only_newly_authorized_datasets():
    user = NS(pk=3)
    chat = NS(pk=1, owner_id=3, scope="")
    entry = _entry()
    pricing = {"openai_api:synthetic-model": {"usd_micros": 30_000, "unit": "openai_container_20m"}}
    async def authorize(): return True
    def policy(_chat, _user, turn, _entry):
        return {"enabled": True, "datasets": [{**_dataset(), "content": f"turn={turn.pk}\n".encode()}]}
    async def prepare(turn_id):
        turn = NS(pk=turn_id, driver="openai_api", model="synthetic-model")
        with override_settings(DCM_ASSISTANT_EXECUTION_POLICY=policy, DCM_ASSISTANT_EXECUTION_PRICING=pricing):
            return await prepare_execution(chat, user, turn, entry, authorize=authorize)
    first = async_to_sync(prepare)(1)
    second = async_to_sync(prepare)(2)
    assert first.datasets[0].content == b"turn=1\n"
    assert second.datasets[0].content == b"turn=2\n"
    assert b"turn=1" not in second.datasets[0].content


def test_bounded_csv_json_outputs_and_unrelated_artifacts_are_validated():
    result = validate_outputs([
        {"name": "table.csv", "media_type": "text/csv", "content": b"x,y\n1,2\n"},
        {"name": "values.json", "media_type": "application/json", "content": b"{\"n\":2}"},
    ])
    assert result[0]["data"].startswith("x,y") and result[1]["data"] == {"n": 2}
    with pytest.raises(DriverError):
        validate_outputs([{"name": "page.html", "media_type": "text/html", "content": b"<b>x</b>"}])
    with pytest.raises(DriverError):
        validate_outputs([{"name": "bad.json", "media_type": "application/json", "content": b"not-json"}])


def test_catalogue_support_is_explicit_and_cli_or_unpriced_entries_are_unavailable():
    openai = _entry()
    pricing = {"openai_api:synthetic-model": {"usd_micros": 30_000, "unit": "openai_container_20m"}}
    with override_settings(DCM_ASSISTANT_EXECUTION_PRICING=pricing):
        assert model_supports_execution(openai)
        assert not model_supports_execution({**openai, "model": "unknown-model"})
        assert not model_supports_execution({"driver":"claude_cli", "model":"synthetic-model",
            "code_execution":{"enabled":True,"tool_version":"code_interpreter"}})
    with override_settings(DCM_ASSISTANT_EXECUTION_PRICING={}):
        assert not model_supports_execution(openai)


class _Body:
    def __init__(self, content): self.content = content
    def read(self): return self.content


class _OpenAIItem:
    def __init__(self, container_id):
        self.id = "call-exec"
        self.type = "code_interpreter_call"
        self.container_id = container_id
        self.code = "print('file-generated-1')"
        self.outputs = [NS(type="logs", logs="analysis complete")]
    def model_dump(self, exclude_none=True): return {"type": self.type, "id": self.id, "code": self.code}


class _OpenAIClient:
    def __init__(self):
        self.requests = []
        self.containers_created = []
        self.containers_deleted = []
        self.files_deleted = []
        self.current = None
        owner = self
        class Files:
            async def create(self, **kwargs):
                assert kwargs["purpose"] == "assistants"
                return NS(id="file-input-1")
            async def delete(self, file_id): owner.files_deleted.append(file_id)
        class Content:
            async def retrieve(self, file_id, *, container_id):
                assert file_id == "file-generated-1" and container_id == owner.current
                return _Body(b"{\"count\":2}")
        class ContainerFiles:
            async def retrieve(self, file_id, *, container_id):
                return NS(path="/mnt/data/result.json", bytes=11, source="assistant", container_id=container_id)
        class Containers:
            files = NS(retrieve=ContainerFiles().retrieve, content=NS(retrieve=Content().retrieve))
            async def create(self, **kwargs):
                assert kwargs["network_policy"] == {"type": "disabled"}
                assert kwargs["memory_limit"] == "1g"
                owner.current = f"container-{len(owner.containers_created) + 1}"
                owner.containers_created.append((owner.current, kwargs))
                return NS(id=owner.current)
            async def delete(self, container_id): owner.containers_deleted.append(container_id)
        class Responses:
            async def create(self, **kwargs):
                owner.requests.append(copy.deepcopy(kwargs))
                item = _OpenAIItem(owner.current) if any(row.get("type") == "code_interpreter" for row in (kwargs.get("tools") or [])) else None
                async def events():
                    citation = NS(type="container_file_citation", file_id="file-generated-1", container_id=owner.current)
                    message = NS(type="message", content=[NS(type="output_text", annotations=[citation])])
                    if item:
                        yield NS(type="response.output_text.delta", delta="call-exec file-generated-1")
                    yield NS(type="response.completed", response=NS(
                        usage=NS(input_tokens=4, output_tokens=2), output=[item, message] if item else []))
                return events()
        self.files, self.containers, self.responses = Files(), Containers(), Responses()


def test_openai_uses_explicit_disabled_network_container_captures_and_cleans_current_analysis(monkeypatch):
    from django_core_micha.assistant.drivers import openai_api
    from django_core_micha.extraction import cost_guard
    client = _OpenAIClient()
    reserve_calls, settled = [], []
    monkeypatch.setattr(openai_api, "_reserve", lambda *args: None)
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(openai_api, "release_reservation", lambda *args, **kwargs: None)
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: reserve_calls.append(kwargs) or object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: settled.append(kwargs))
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    driver = openai_api.OpenAIDriver(client)
    async def one_analysis():
        config = _config()
        try:
            return [event async for event in driver.stream(model="synthetic-model", messages=[], user=NS(pk=2),
                chat=NS(pk=4), execution=config)]
        finally:
            await driver.finish_execution(config)
    first = async_to_sync(one_analysis)()
    second = async_to_sync(one_analysis)()
    for events in (first, second):
        record = next(row["record"] for row in events if row["type"] == "execution")
        assert record["code"] == "print('[provider reference]')"
        assert record["output_text"] == "analysis complete"
        assert record["data"] == [{"name": "result.json", "media_type": "application/json", "data": {"count": 2}}]
        assert not any(any(ref in json.dumps(row) for ref in ("container-", "file-input-", "file-generated-1", "call-exec")) for row in events)
    assert [row[0] for row in client.containers_created] == ["container-1", "container-2"]
    assert client.containers_deleted == ["container-1", "container-2"]
    assert client.files_deleted == ["file-input-1", "file-input-1"]
    assert all(row[1]["file_ids"] == ["file-input-1"] for row in client.containers_created)
    assert len(reserve_calls) == len(settled) == 2
    assert all(row["tools"][0]["type"] == "code_interpreter" for row in client.requests)
    assert all(row["tools"][0]["container"] == client.containers_created[index][0] for index, row in enumerate(client.requests))
    assert all(event["usage"].get("execution_usd_micros") == 30_000
               for events in (first, second) for event in events if event["type"] == "usage")


def test_openai_ordinary_turn_does_not_open_execution_resources(monkeypatch):
    from django_core_micha.assistant.drivers import openai_api
    client = _OpenAIClient()
    driver = openai_api.OpenAIDriver(client)
    async def run():
        return [event async for event in driver.stream(model="synthetic-model", messages=[], user=NS(pk=2), chat=NS(pk=4))]
    async_to_sync(run)()
    assert client.containers_created == [] and client.files_deleted == []
    assert client.requests[0]["tools"] is None


@pytest.mark.parametrize("scenario", ["replay", "limit", "foreign_artifact"])
def test_openai_replay_limits_and_foreign_container_fail_closed(monkeypatch, scenario):
    from django_core_micha.assistant.drivers import openai_api
    from django_core_micha.extraction import cost_guard
    client = _OpenAIClient()
    monkeypatch.setattr(openai_api, "_reserve", lambda *args: None)
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: None)
    async def create(**kwargs):
        first = _OpenAIItem(client.current)
        second = _OpenAIItem(client.current)
        if scenario == "limit": second.id = "call-two"
        outputs = [first, second] if scenario != "foreign_artifact" else [first,
            NS(type="message", content=[NS(annotations=[NS(type="container_file_citation", file_id="file-x", container_id="foreign")])])]
        async def events():
            yield NS(type="response.completed", response=NS(usage=NS(input_tokens=1, output_tokens=1), output=outputs))
        return events()
    client.responses.create = create
    config = _config()
    if scenario == "limit":
        from dataclasses import replace
        config = replace(config, max_continuations=1)
    driver = openai_api.OpenAIDriver(client)
    async def run():
        try:
            return [event async for event in driver.stream(model="synthetic-model", messages=[], user=NS(pk=2), chat=NS(pk=4), execution=config)]
        finally:
            await driver.finish_execution(config)
    if scenario == "replay":
        events = async_to_sync(run)()
        assert sum(event["type"] == "execution" for event in events) == 1
    else:
        with pytest.raises(DriverError, match="execution_limit|execution_output_invalid"):
            async_to_sync(run)()
    assert client.containers_deleted == ["container-1"]


def test_rejected_execution_reservation_opens_no_provider_resource(monkeypatch):
    from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
    from django_core_micha.extraction import cost_guard
    client = _OpenAIClient()
    def reject(**kwargs):
        raise cost_guard.AICostLimitExceeded(user_id=2, used_cents=5, limit_cents=5)
    monkeypatch.setattr(cost_guard, "reserve_user_budget", reject)
    async def run():
        return [event async for event in OpenAIDriver(client).stream(model="synthetic-model", messages=[], user=NS(pk=2), execution=_config())]
    with pytest.raises(DriverError, match="quota_exceeded"):
        async_to_sync(run)()
    assert not client.containers_created and not client.requests


def test_cleanup_failure_is_observable_without_provider_contents(monkeypatch, caplog):
    from django_core_micha.assistant.drivers import openai_api
    client = _OpenAIClient()
    async def fail(_container):
        raise RuntimeError("private provider details")
    monkeypatch.setattr(client.containers, "delete", fail)
    async_to_sync(openai_api._cleanup_execution)(client, "container-1", ["input"])
    assert "container cleanup failed" in caplog.text
    assert "private provider details" not in caplog.text
    assert client.files_deleted == ["input"]


def test_openai_ambiguous_provider_failure_is_not_retried_and_logs_no_code(monkeypatch, caplog):
    from django_core_micha.assistant.drivers import openai_api
    from django_core_micha.extraction import cost_guard
    client = _OpenAIClient()
    requests, settlements = [], []
    async def fail(**kwargs):
        requests.append(kwargs)
        raise RuntimeError("PRIVATE DATA AND EXECUTED CODE")
    client.responses.create = fail
    monkeypatch.setattr(openai_api, "_reserve", lambda *args: None)
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(openai_api, "release_reservation", lambda *args, **kwargs: None)
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: settlements.append(True))
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    driver = openai_api.OpenAIDriver(client)
    config = _config()
    async def run():
        try:
            return [event async for event in driver.stream(
                model="synthetic-model", messages=[], user=NS(pk=2), chat=NS(pk=4), execution=config)]
        finally:
            await driver.finish_execution(config)
    with pytest.raises(DriverError) as error:
        async_to_sync(run)()
    assert error.value.execution_cost_estimate == 30_000
    assert len(requests) == 1 and settlements == [True]
    assert client.containers_deleted == ["container-1"]
    assert "PRIVATE DATA AND EXECUTED CODE" not in caplog.text


def test_openai_timeout_cleans_container_and_settles_conservative_execution_estimate(monkeypatch):
    from django_core_micha.assistant.drivers import openai_api
    from django_core_micha.extraction import cost_guard
    client = _OpenAIClient()
    settled = []
    async def hanging(**kwargs):
        async def events():
            await asyncio.Event().wait()
            yield NS(type="response.completed", response=NS(usage=NS(input_tokens=1, output_tokens=1), output=[]))
        return events()
    client.responses.create = hanging
    monkeypatch.setattr(openai_api, "_reserve", lambda *args: None)
    monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(openai_api, "release_reservation", lambda *args, **kwargs: None)
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: settled.append(True))
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    driver = openai_api.OpenAIDriver(client)
    config = _config()
    async def run():
        try:
            return [event async for event in driver.stream(
                model="synthetic-model", messages=[], user=NS(pk=2), chat=NS(pk=4), execution=config)]
        finally:
            await driver.finish_execution(config)
    async def timed(): return await asyncio.wait_for(run(), 0.02)
    with pytest.raises(TimeoutError): async_to_sync(timed)()
    assert client.containers_deleted == ["container-1"] and settled == [True]


class _AnthropicStream:
    def __init__(self, events, message): self.events, self.message = events, message
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    def __aiter__(self):
        async def generate():
            for event in self.events: yield event
        return generate()
    async def get_final_message(self): return self.message


class _AnthropicClient:
    def __init__(self):
        self.requests = []
        self.uploaded = []
        self.deleted = []
        self.downloaded = []
        owner = self
        class BetaFiles:
            async def upload(self, **kwargs):
                assert kwargs["expires_in_seconds"] == 3600
                owner.uploaded.append(kwargs)
                return NS(id="anthropic-input-file")
            async def retrieve_metadata(self, file_id, **kwargs):
                return NS(filename="summary.json", mime_type="application/json", size_bytes=7)
            async def download(self, file_id, **kwargs):
                owner.downloaded.append(file_id)
                return _Body(b"{\"n\":1}")
            async def delete(self, file_id, **kwargs): owner.deleted.append(file_id)
        class Messages:
            def stream(self, **kwargs):
                owner.requests.append(copy.deepcopy(kwargs))
                index = len(owner.requests)
                container_id = "anthropic-container"
                if index == 1:
                    events = [
                        NS(type="message_start", message=NS(container=NS(id=container_id))),
                        NS(type="content_block_start", index=0, content_block=NS(type="server_tool_use", id="srv-1", name="bash_code_execution", input={})),
                        NS(type="content_block_delta", index=0, delta=NS(type="input_json_delta", partial_json='{"command":"print(1)"}')),
                        NS(type="content_block_stop", index=0),
                        NS(type="content_block_start", index=1, content_block=NS(type="bash_code_execution_tool_result", tool_use_id="srv-1", content=NS(type="bash_code_execution_result", stdout="ran", stderr="", return_code=0, content=[NS(type="file", file_id="anthropic-output-file")]))) ,
                    ]
                    content = [NS(type="server_tool_use", id="srv-1", name="bash_code_execution", input={"command":"print(1)"}),
                        NS(type="bash_code_execution_tool_result", tool_use_id="srv-1", content={"stdout":"ran", "stderr":"", "return_code":0, "content":[{"type":"file", "file_id":"anthropic-output-file"}]}),
                        NS(type="tool_use", id="tool-1", name="lookup", input={})]
                else:
                    events = [NS(type="message_start", message=NS(container=NS(id=container_id))),
                        NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text="done"))]
                    content = [NS(type="text", text="done")]
                message = NS(content=content, container=NS(id=container_id),
                    usage=NS(input_tokens=3, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                        server_tool_use=NS(code_execution_requests=1 if index == 1 else 0)))
                return _AnthropicStream(events, message)
        self.messages = Messages()
        self.beta = NS(files=BetaFiles())


def test_anthropic_reuses_container_only_inside_analysis_and_deduplicates_billing(monkeypatch):
    from django_core_micha.assistant.drivers import anthropic_api
    from django_core_micha.assistant.drivers import base
    from django_core_micha.extraction import cost_guard
    client = _AnthropicClient()
    fixed_reservations, settled = [], []
    monkeypatch.setattr(base, "_reserve", lambda *args: None)
    monkeypatch.setattr(cost_guard, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(base, "charge_aborted", lambda **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: fixed_reservations.append(kwargs) or object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: settled.append(kwargs))
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    tool = ToolSpec("lookup", "Lookup", "trusted app tool", {"type":"object"},
        lambda *_: ("app result", []))
    config = ExecutionConfig("anthropic_api", "synthetic-model", "code_execution_20250825",
        "1g", 90, 3, 30_000, (Dataset("source.csv", "synthetic", b"x\n1\n", "text/csv"),))
    driver = anthropic_api.AnthropicDriver(client)
    config = ExecutionConfig("anthropic_api", "synthetic-model", "code_execution_20250825",
        "1g", 90, 3, 30_000, (Dataset("source.csv", "synthetic", b"x\n1\n", "text/csv"),))
    async def one_analysis():
        try:
            return [event async for event in driver.stream(model="synthetic-model", messages=[{"role":"user","content":"analyze"}],
                tools=[tool], user=NS(pk=5), execution=config)]
        finally:
            await driver.finish_execution(config)
    events = async_to_sync(one_analysis)()
    assert client.requests[0]["tools"][-1] == {"type":"code_execution_20250825", "name":"code_execution"}
    assert "container" not in client.requests[0]
    assert client.requests[1]["container"] == {"id":"anthropic-container"}
    assert any(block.get("type") == "container_upload" for block in client.requests[0]["messages"][-1]["content"])
    assert sum(event["type"] == "execution" for event in events) == 1
    assert sum(event.get("usage", {}).get("execution_usd_micros", 0) for event in events) == 30_000
    assert sum(event.get("usage", {}).get("execution_requests", 0) for event in events) == 1
    assert len(fixed_reservations) == len(settled) == 1
    record = next(row["record"] for row in events if row["type"] == "execution")
    assert record["code"] == "print(1)"
    assert record["data"] == [{"name":"summary.json", "media_type":"application/json", "data":{"n":1}}]
    assert "anthropic-output-file" not in json.dumps(record)
    assert client.downloaded == ["anthropic-output-file"]
    assert set(client.deleted) == {"anthropic-input-file", "anthropic-output-file"}


def test_anthropic_resumes_paused_execution_with_same_container(monkeypatch):
    from django_core_micha.assistant.drivers import anthropic_api, base
    from django_core_micha.extraction import cost_guard
    client = _AnthropicClient()
    original_stream = client.messages.stream
    def paused_stream(**kwargs):
        stream = original_stream(**kwargs)
        if len(client.requests) == 1:
            stream.message.content = stream.message.content[:-1]
            stream.message.stop_reason = "pause_turn"
        return stream
    monkeypatch.setattr(client.messages, "stream", paused_stream)
    monkeypatch.setattr(base, "_reserve", lambda *args: None)
    monkeypatch.setattr(cost_guard, "settle_reservation", lambda **kwargs: None)
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: None)
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    config = ExecutionConfig("anthropic_api", "synthetic-model", "code_execution_20250825",
        "1g", 90, 3, 30_000, (Dataset("source.csv", "synthetic", b"x\n1\n", "text/csv"),))
    driver = anthropic_api.AnthropicDriver(client)
    async def run():
        try:
            return [event async for event in driver.stream(model="synthetic-model",
                messages=[{"role": "user", "content": "analyze"}], user=NS(pk=5), execution=config)]
        finally:
            await driver.finish_execution(config)
    async_to_sync(run)()
    assert client.requests[1]["container"] == {"id": "anthropic-container"}
    assert client.requests[1]["messages"][-1]["role"] == "assistant"
    assert client.requests[1]["messages"][-1]["content"][0]["type"] == "server_tool_use"


@pytest.mark.django_db
def test_private_turn_persists_execution_results_and_cost_shared_payload_hides_results():
    from datetime import timedelta
    from django.utils import timezone
    from django_core_micha.assistant.limit import used_usd_micros
    from django_core_micha.billing.models import AICostRecord
    owner = get_user_model().objects.create_user(username="execution-owner", password="x")
    private = AssistantChat.objects.create(owner=owner)
    turn = AssistantTurn.objects.create(chat=private, sender=owner, sequence=1, user_text="analyze",
        driver="openai_api", model="synthetic-model")
    rows = [{"code":"print(1)", "status":"completed", "output_text":"ok", "data":[{"name":"x.json","media_type":"application/json","data":{"n":1}}]}]
    _finish(turn.pk, "answer", "completed", usage={"input_tokens":2, "output_tokens":1,
        "execution_usd_micros":30_000}, execution_results=rows)
    saved = AssistantTurn.objects.select_related("chat").get(pk=turn.pk)
    assert saved.cost_usd_micros >= 30_000
    assert used_usd_micros(owner, timezone.now() - timedelta(days=1), timezone.now() + timedelta(days=1)) >= 30_000
    assert AICostRecord.objects.get(reference=str(saved.pk)).cost_usd_micros >= 30_000
    received = []
    from django_core_micha.assistant.turns import _invoke_usage_hook
    with override_settings(DCM_ASSISTANT_USAGE_HOOK=lambda chat, user, turn, usage: received.append(usage)):
        _invoke_usage_hook(saved.pk)
    assert received[0]["execution_usd_micros"] == 30_000
    assert turn_payload(saved)["execution_results"] == rows
    shared = AssistantChat.objects.create(owner=owner, scope="team")
    shared_turn = AssistantTurn.objects.create(chat=shared, sender=owner, sequence=1, user_text="analyze",
        driver="openai_api", model="synthetic-model", execution_results=rows)
    with override_settings(DCM_ASSISTANT_SHARED_SCOPE=lambda scope: scope == "team"):
        assert "execution_results" not in turn_payload(shared_turn)


@pytest.mark.django_db
def test_chat_access_loss_before_persist_suppresses_provider_execution_results(monkeypatch):
    from django.test import override_settings
    from django_core_micha.assistant import turns
    from django_core_micha.assistant.turns import AccessLost
    owner = get_user_model().objects.create_user(username="execution-revoked", password="x")
    chat = AssistantChat.objects.create(owner=owner, scope="private-data", selected_driver="openai_api",
        selected_model="synthetic-model", selected_effort="low")
    entry = _entry()
    entry["name"] = "synthetic"
    entry["efforts"] = ["low"]
    entry["billing"] = "pay_per_use"
    entry["default"] = True
    validity_calls = []
    def valid(*_):
        validity_calls.append(True)
        return len(validity_calls) < 3
    class Driver:
        async def stream(self, **kwargs):
            yield {"type":"execution", "record":{"code":"print(1)", "status":"completed", "output_text":"private", "data":[]}}
            yield {"type":"usage", "usage":{"input_tokens":1, "output_tokens":1, "execution_usd_micros":30_000}}
    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("synthetic-key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_: [])
    monkeypatch.setattr(turns, "_make_driver", lambda *_: Driver())
    frames = []
    async def emit(frame): frames.append(frame)
    pricing = {"openai_api:synthetic-model":{"usd_micros":30_000,"unit":"openai_container_20m"}}
    with override_settings(DCM_ASSISTANT_CATALOGUE=[entry], DCM_ASSISTANT_EXECUTION_PRICING=pricing,
        DCM_ASSISTANT_EXECUTION_POLICY=lambda *_:{"enabled":True,"datasets":[]},
        DCM_ASSISTANT_TURN_VALIDITY=valid, DCM_ASSISTANT_SCOPE_PERMISSION=lambda *_:True, DCM_ASSISTANT_SHARED_SCOPE=lambda _:False,
        DCM_ASSISTANT_LIMITS={"access_recheck_seconds":60}):
        with pytest.raises(AccessLost):
            async_to_sync(turns.run_turn)(chat, owner, "analyze", emit)
    saved = AssistantTurn.objects.get(chat=chat)
    assert saved.state == "cancelled" and saved.execution_results == []
    assert not frames or not frames[-1]["turn"]["execution_results"]
