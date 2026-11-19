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
    bounded_text, bounded_text_parts, read_bounded_response, validate_outputs,
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


@pytest.mark.parametrize("field,value", [
    ("content", b"a,b\n1,changed\n"),
    ("data_class", "restricted"),
    ("media_type", "application/csv"),
])
def test_changes_to_an_uploaded_dataset_snapshot_fail_closed(field, value):
    from dataclasses import replace
    from django_core_micha.assistant.execution import (
        mark_execution_dataset_uploaded, pending_execution_datasets,
    )

    config = _config()
    original = config.datasets[0]
    mark_execution_dataset_uploaded(config.session, original)
    changed = replace(original, **{field: value})
    with pytest.raises(DriverError, match="execution_unavailable"):
        pending_execution_datasets(config.session, (changed,))


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


def test_output_limits_preserve_valid_csv_json_and_text_with_truncation_metadata():
    import csv
    from io import StringIO
    limits = {"max_execution_output_files": 4, "max_execution_output_file_bytes": 8,
        "max_execution_output_total_bytes": 16}
    with override_settings(DCM_ASSISTANT_LIMITS=limits):
        files, summary = validate_outputs([
            {"name": "rows.csv", "media_type": "text/csv", "content": b"x,y\n1,2\n3,4\n"},
            {"name": "large.json", "media_type": "application/json", "content": b"{\"n\":123}"},
            {"name": "note.txt", "media_type": "text/plain", "content": "é🙂z".encode()},
        ], return_summary=True)
    assert files[0]["data"] == "x,y\n1,2\n"
    assert list(csv.reader(StringIO(files[0]["data"]))) == [["x", "y"], ["1", "2"]]
    assert files[0]["truncated"] is True and files[0]["omitted_bytes"] == 4
    assert files[1]["data"] is None and files[1]["truncated"] is True
    assert files[1]["omitted_bytes"] == 9
    assert files[2]["data"] == "é"
    assert files[2]["truncated"] is True
    assert summary["truncated"] is True


def test_output_aggregate_and_file_count_limits_are_bounded_and_reported():
    with override_settings(DCM_ASSISTANT_LIMITS={"max_execution_output_files": 2,
            "max_execution_output_file_bytes": 20, "max_execution_output_total_bytes": 10}):
        files, aggregate = validate_outputs([
            {"name": "first.txt", "media_type": "text/plain", "content": b"12345678"},
            {"name": "second.txt", "media_type": "text/plain", "content": b"abcdefgh"},
        ], return_summary=True)
        counted, file_count = validate_outputs([
            {"name": "a.txt", "media_type": "text/plain", "content": b"a"},
            {"name": "b.txt", "media_type": "text/plain", "content": b"b"},
            {"name": "c.txt", "media_type": "text/plain", "content": b"c"},
        ], return_summary=True)
    assert [row["data"] for row in files] == ["12345678", "ab"]
    assert files[1]["truncated"] is True and files[1]["omitted_bytes"] == 6
    assert aggregate["omitted_bytes"] == 6
    assert len(counted) == 2 and file_count["omitted_files"] == 1
    assert file_count["truncated"] is True


def test_csv_truncation_drops_partial_and_quoted_multiline_rows():
    import csv
    from io import StringIO
    with override_settings(DCM_ASSISTANT_LIMITS={"max_execution_output_files": 2,
            "max_execution_output_file_bytes": 12, "max_execution_output_total_bytes": 100}):
        mid_row, _ = validate_outputs([
            {"name": "rows.csv", "media_type": "text/csv", "content": b"x,y\n1,2\n3,4,5\n"}], return_summary=True)
        quoted, _ = validate_outputs([
            {"name": "q.csv", "media_type": "text/csv", "content": b'a\n"b\nc"\n"d\ne"\n'}], return_summary=True)
    assert mid_row[0]["data"] == "x,y\n1,2\n" and mid_row[0]["truncated"] is True
    assert quoted[0]["data"] == 'a\n"b\nc"\n' and quoted[0]["truncated"] is True
    assert list(csv.reader(StringIO(quoted[0]["data"]))) == [["a"], ["b\nc"]]


def test_output_limits_accumulate_across_continuations_through_the_session():
    from django_core_micha.assistant.execution import ExecutionSession
    session = ExecutionSession()
    with override_settings(DCM_ASSISTANT_LIMITS={"max_execution_output_files": 2,
            "max_execution_output_file_bytes": 20, "max_execution_output_total_bytes": 10}):
        first, _ = validate_outputs([
            {"name": "a.txt", "media_type": "text/plain", "content": b"123456"}], session=session, return_summary=True)
        second, summary = validate_outputs([
            {"name": "b.txt", "media_type": "text/plain", "content": b"abcdefgh"}], session=session, return_summary=True)
        third, third_summary = validate_outputs([
            {"name": "c.txt", "media_type": "text/plain", "content": b"z"}], session=session, return_summary=True)
    assert first[0]["data"] == "123456"
    assert second[0]["data"] == "abcd" and second[0]["truncated"] is True
    assert summary["truncated"] is True
    assert third == [] and third_summary["omitted_files"] == 1


def test_bounded_text_keeps_multibyte_boundaries_and_reports_exact_omissions():
    result, metadata = bounded_text("Aé🙂Z", 6)
    assert result == "Aé"
    assert result.encode("utf-8").decode("utf-8") == result
    assert metadata == {"truncated": True, "omitted_bytes": 5}


def test_bounded_text_parts_counts_large_omissions_without_encoding_the_tail():
    class NoEncode(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("provider text should not be encoded into a full byte copy")

    tail = NoEncode("z" * (2 * 1024 * 1024))
    result, metadata = bounded_text_parts(["ab", tail], 1)
    assert result == "a"
    assert metadata == {"truncated": True, "omitted_bytes": len(tail) + 1}


def test_json_output_with_utf8_bom_remains_valid():
    from django_core_micha.assistant.execution import _limits, _validate_dataset

    source = b"\xef\xbb\xbf{\"n\":1}"
    assert _validate_dataset({"name": "source.json", "data_class": "synthetic",
        "content": source, "media_type": "application/json"}, _limits()).content == source
    result = validate_outputs([{
        "name": "bom.json", "media_type": "application/json",
        "content": source,
    }])
    assert result[0]["data"] == {"n": 1}


def test_malformed_csv_output_is_rejected():
    malformed = b"column\n\"unterminated\n"
    with pytest.raises(DriverError, match="execution_output_invalid"):
        validate_outputs([{"name": "bad.csv", "media_type": "text/csv", "content": malformed}])


def test_invalid_json_constants_and_invalid_utf8_text_are_rejected():
    with pytest.raises(DriverError, match="execution_output_invalid"):
        validate_outputs([{"name": "bad.json", "media_type": "application/json", "content": b"NaN"}])
    with pytest.raises(DriverError, match="execution_output_invalid"):
        validate_outputs([{"name": "bad.txt", "media_type": "text/plain", "content": b"\xff"}])


def test_streaming_output_reader_stops_after_the_bound():
    consumed = []

    class Stream:
        def iter_bytes(self, chunk_size):
            async def generate():
                consumed.append("first")
                yield b"1234567890"[:chunk_size]
                consumed.append("next")
                yield b"unread"
            return generate()
    content, truncated = async_to_sync(read_bounded_response)(Stream(), 5)
    assert content == b"12345" and truncated is True
    assert consumed == ["first"]


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
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    def iter_bytes(self, chunk_size=None):
        size = chunk_size or max(1, len(self.content))
        async def generate():
            for offset in range(0, len(self.content), size):
                yield self.content[offset:offset + size]
        return generate()


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
        self.files_created = []
        self.container_files_created = []
        self.current = None
        owner = self
        class Files:
            async def create(self, **kwargs):
                assert kwargs["purpose"] == "assistants"
                owner.files_created.append(kwargs)
                return NS(id=f"file-input-{len(owner.files_created)}")
            async def delete(self, file_id): owner.files_deleted.append(file_id)
        class Content:
            def retrieve(self, file_id, *, container_id):
                assert file_id == "file-generated-1" and container_id == owner.current
                return _Body(b"{\"count\":2}")
        class ContainerFiles:
            async def retrieve(self, file_id, *, container_id):
                return NS(path="/mnt/data/result.json", bytes=11, source="assistant", container_id=container_id)
            async def create(self, container_id, *, file):
                owner.container_files_created.append((container_id, file))
                return NS(id=f"container-input-{len(owner.container_files_created)}", container_id=container_id)
        class Containers:
            container_files = ContainerFiles()
            files = NS(retrieve=container_files.retrieve, create=container_files.create,
                content=NS(with_streaming_response=NS(retrieve=Content().retrieve)))
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
    assert client.files_deleted == ["file-input-1", "file-input-2"]
    assert [row[1]["file_ids"] for row in client.containers_created] == [["file-input-1"], ["file-input-2"]]
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


def test_openai_output_tracking_does_not_retain_ids_beyond_the_file_limit():
    from django_core_micha.assistant.drivers import openai_api
    from django_core_micha.assistant.execution import ExecutionSession

    class CountedID(str):
        def __new__(cls, value):
            instance = super().__new__(cls, value)
            instance.hash_calls = 0
            return instance

        def __hash__(self):
            self.hash_calls += 1
            return super().__hash__()

    class ContainerFiles:
        def __init__(self):
            self.retrieved = []
            self.content = NS(with_streaming_response=NS(
                retrieve=lambda *_args, **_kwargs: _Body(b"ok\n")))

        async def retrieve(self, file_id, *, container_id):
            self.retrieved.append(file_id)
            return NS(path="/tmp/result.txt", bytes=3, source="assistant", container_id=container_id)

    container_files = ContainerFiles()
    client = NS(containers=NS(files=container_files))
    file_ids = [CountedID(f"generated-{index}") for index in range(4)]
    outputs = [{"type": "files", "files": [{"file_id": file_id} for file_id in file_ids]}]
    session = ExecutionSession()

    async def download():
        with override_settings(DCM_ASSISTANT_LIMITS={"max_execution_output_files": 1}):
            return await openai_api._download_execution_files(
                client, "container", outputs, [], session)

    result, tracked_ids, summary = async_to_sync(download)()
    assert len(result) == 1 and len(tracked_ids) == 1
    assert container_files.retrieved == [file_ids[0]]
    assert [file_id.hash_calls for file_id in file_ids] == [2, 1, 1, 1]
    assert summary["omitted_files"] == 3


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
        self.upload_count = 0
        self.lookup_uses = 1
        owner = self
        class BetaFiles:
            async def upload(self, **kwargs):
                assert kwargs["expires_in_seconds"] == 3600
                owner.uploaded.append(kwargs)
                owner.upload_count += 1
                file_id = "anthropic-input-file" if owner.upload_count == 1 else f"anthropic-input-file-{owner.upload_count}"
                return NS(id=file_id)
            async def retrieve_metadata(self, file_id, **kwargs):
                return NS(filename="summary.json", mime_type="application/json", size_bytes=7)
            async def download(self, file_id, **kwargs):
                owner.downloaded.append(file_id)
                return _Body(b"{\"n\":1}")
            def stream_download(self, file_id, **kwargs):
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
                        *[NS(type="tool_use", id=f"tool-{tool_index}", name="lookup", input={})
                          for tool_index in range(1, owner.lookup_uses + 1)]]
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
        self.beta.files.with_streaming_response = NS(download=self.beta.files.stream_download)


def test_anthropic_download_error_still_tracks_later_generated_files_for_cleanup(caplog):
    from dataclasses import replace
    from django_core_micha.assistant.drivers import anthropic_api
    from django_core_micha.assistant.execution import ExecutionSession

    class Files:
        def __init__(self):
            self.metadata_ids = []
            self.delete_attempts = []

        async def retrieve_metadata(self, file_id, **kwargs):
            self.metadata_ids.append(file_id)
            raise RuntimeError("bad-generated private content")

        async def delete(self, file_id, **kwargs):
            self.delete_attempts.append(file_id)
            if file_id == "later-generated" and self.delete_attempts.count(file_id) == 1:
                raise RuntimeError("later-generated private content")

    files = Files()
    client = NS(beta=NS(files=files))
    session = ExecutionSession()
    config = replace(_config("anthropic_api", datasets=[]), session=session)
    driver = anthropic_api.AnthropicDriver(client)

    async def run():
        with override_settings(DCM_ASSISTANT_LIMITS={"max_execution_output_files": 1}):
            with pytest.raises(DriverError, match="execution_output_invalid"):
                await anthropic_api._download_files(
                    client, ["bad-generated", "later-generated"], ["input-file"], session)
            await driver.finish_execution(config)

    with caplog.at_level("WARNING"):
        async_to_sync(run)()
    assert files.metadata_ids == ["bad-generated"]
    assert files.delete_attempts == ["later-generated", "bad-generated", "later-generated"]
    assert len(session.provider_file_ids) <= 2
    assert "bad-generated" not in caplog.text
    assert "later-generated" not in caplog.text
    assert "private content" not in caplog.text


def test_anthropic_excess_output_delete_failure_is_retried_during_final_cleanup(caplog):
    from dataclasses import replace
    from django_core_micha.assistant.drivers import anthropic_api
    from django_core_micha.assistant.execution import ExecutionSession

    class Files:
        def __init__(self):
            self.metadata_ids = []
            self.delete_attempts = []
            self.with_streaming_response = NS(download=lambda file_id, **kwargs: _Body(b"ok\n"))

        async def retrieve_metadata(self, file_id, **kwargs):
            self.metadata_ids.append(file_id)
            return NS(filename="result.txt", mime_type="text/plain", size_bytes=3)

        async def delete(self, file_id, **kwargs):
            self.delete_attempts.append(file_id)
            if file_id == "excess-generated" and self.delete_attempts.count(file_id) == 1:
                raise RuntimeError("excess-generated private content")

    files = Files()
    client = NS(beta=NS(files=files))
    session = ExecutionSession()
    config = replace(_config("anthropic_api", datasets=[]), session=session)
    driver = anthropic_api.AnthropicDriver(client)

    async def run():
        with override_settings(DCM_ASSISTANT_LIMITS={"max_execution_output_files": 1}):
            result, summary = await anthropic_api._download_files(
                client, ["accepted-generated", "excess-generated"], [], session)
            await driver.finish_execution(config)
            return result, summary

    with caplog.at_level("WARNING"):
        result, summary = async_to_sync(run)()
    assert [row["name"] for row in result] == ["result.txt"]
    assert summary["omitted_files"] == 1
    assert files.metadata_ids == ["accepted-generated"]
    assert files.delete_attempts == ["excess-generated", "accepted-generated", "excess-generated"]
    assert len(session.provider_file_ids) <= 2
    assert "excess-generated" not in caplog.text
    assert "private content" not in caplog.text


def _refresh_policy_rows():
    return [
        {"name": "prior-a.csv", "data_class": "synthetic", "content": b"a\n1\n", "media_type": "text/csv"},
        {"name": "prior-b.csv", "data_class": "synthetic", "content": b"b\n2\n", "media_type": "text/csv"},
    ]


def _refresh_context(driver, scenario="allow"):
    user = NS(pk=41)
    chat = NS(pk=52, owner_id=user.pk, scope="")
    turn = NS(pk=63, driver=driver, model="synthetic-model")
    entry = _entry(driver, version="code_interpreter" if driver == "openai_api" else "code_execution_20250825")
    pricing = {f"{driver}:synthetic-model": {"usd_micros": 30_000,
        "unit": "openai_container_20m" if driver == "openai_api" else "anthropic_execution_5m"}}
    state = {"fetched": False, "tool_calls": 0}
    observed = []
    prior = _refresh_policy_rows()
    fresh = {"name": "fresh.csv", "data_class": "synthetic", "content": b"fresh\n3\n", "media_type": "text/csv"}
    foreign = {"name": "foreign.csv", "data_class": "synthetic", "content": b"secret\n", "media_type": "text/csv"}

    def policy(current_chat, current_user, _turn, _entry):
        observed.append((current_chat.pk, current_user.pk))
        if current_chat.pk != chat.pk or current_user.pk != user.pk:
            rows = [foreign]
        elif not state["fetched"]:
            rows = prior
        elif scenario == "deny":
            return {"enabled": False}
        elif scenario == "revoke":
            rows = [fresh]
        else:
            rows = [*prior, fresh]
        result = {"enabled": True, "datasets": rows}
        if driver == "anthropic_api":
            result["anthropic_retention_approval"] = {
                "policy": ANTHROPIC_RETENTION_POLICY, "app": True, "data_classes": ["synthetic"]}
        return result

    return user, chat, turn, entry, pricing, state, observed, policy, fresh


def _patch_provider_execution_accounting(monkeypatch, driver):
    from django_core_micha.assistant.drivers import base, openai_api
    from django_core_micha.extraction import cost_guard
    if driver == "openai_api":
        monkeypatch.setattr(openai_api, "_reserve", lambda *args: None)
        monkeypatch.setattr(openai_api, "settle_reservation", lambda **kwargs: None)
        monkeypatch.setattr(openai_api, "release_unstarted", lambda *args, **kwargs: None)
        monkeypatch.setattr(openai_api, "charge_aborted", lambda **kwargs: asyncio.sleep(0))
    else:
        monkeypatch.setattr(base, "_reserve", lambda *args: None)
        monkeypatch.setattr(cost_guard, "settle_reservation", lambda **kwargs: None)
        monkeypatch.setattr(base, "charge_aborted", lambda **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **kwargs: object())
    monkeypatch.setattr(cost_guard, "settle_fixed_reservation", lambda **kwargs: None)
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)


def _openai_tool_refresh_client(lookup_calls):
    client = _OpenAIClient()
    call_number = 0
    async def create(**kwargs):
        nonlocal call_number
        client.requests.append(copy.deepcopy(kwargs))
        call_number += 1
        async def events():
            if call_number <= lookup_calls:
                item = NS(type="function_call", id=f"item-{call_number}", call_id=f"call-{call_number}",
                    name="lookup", arguments="{}")
                yield NS(type="response.output_item.added", item=item)
                output = [item]
            else:
                output = [_OpenAIItem(client.current)]
            yield NS(type="response.completed", response=NS(
                usage=NS(input_tokens=3, output_tokens=1), output=output))
        return events()
    client.responses.create = create
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("driver", ["openai_api", "anthropic_api"])
def test_run_turn_wires_refresh_execution_to_the_current_chat_and_user(monkeypatch, driver):
    from django.test import override_settings
    from django_core_micha.assistant import turns
    from django_core_micha.assistant.drivers import anthropic_api, openai_api

    user = get_user_model().objects.create_user(username=f"execution-refresh-{driver}", password="x")
    chat = AssistantChat.objects.create(owner=user, scope="", selected_driver=driver,
        selected_model="synthetic-model", selected_effort="low")
    entry = _entry(driver, version=("code_interpreter" if driver == "openai_api"
        else "code_execution_20250825"))
    entry.update({"name": "synthetic", "efforts": ["low"], "billing": "pay_per_use", "default": True})
    price_unit = "openai_container_20m" if driver == "openai_api" else "anthropic_execution_5m"
    pricing = {f"{driver}:synthetic-model": {"usd_micros": 30_000, "unit": price_unit}}
    state = {"fetched": False}
    observed = []
    owned = {"name": "owned.csv", "data_class": "synthetic",
        "content": b"owned\n1\n", "media_type": "text/csv"}
    fresh = {"name": "fresh.csv", "data_class": "synthetic",
        "content": b"fresh\n2\n", "media_type": "text/csv"}
    foreign = {"name": "foreign.csv", "data_class": "synthetic",
        "content": b"foreign-private\n", "media_type": "text/csv"}

    def policy(current_chat, current_user, _turn, _entry):
        observed.append((current_chat.pk, current_user.pk))
        rows = ([foreign] if current_chat.pk != chat.pk or current_user.pk != user.pk else
            [owned, fresh] if state["fetched"] else [owned])
        decision = {"enabled": True, "datasets": rows}
        if driver == "anthropic_api":
            decision["anthropic_retention_approval"] = {
                "policy": ANTHROPIC_RETENTION_POLICY, "app": True,
                "data_classes": ["synthetic"],
            }
        return decision

    def lookup(*_):
        state["fetched"] = True
        return "queried authorized rows", []

    tool = ToolSpec("lookup", "Lookup", "Reads authorized app data", {"type": "object"}, lookup)
    client = _openai_tool_refresh_client(lookup_calls=1) if driver == "openai_api" else _AnthropicClient()
    if driver == "anthropic_api":
        client.lookup_uses = 1
    provider = (openai_api.OpenAIDriver(client) if driver == "openai_api"
        else anthropic_api.AnthropicDriver(client))
    _patch_provider_execution_accounting(monkeypatch, driver)

    async def no_summary(*_args, **_kwargs):
        return None

    monkeypatch.setattr(turns, "resolve_credential", lambda *_: ("synthetic-key", "deployment"))
    monkeypatch.setattr(turns, "build_system_prompt", lambda *_: "system")
    monkeypatch.setattr(turns, "build_turn_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(turns, "_summarize_if_needed", no_summary)
    monkeypatch.setattr(turns, "latest_summary", lambda *_: None)
    monkeypatch.setattr(turns, "build_history", lambda *_: [])
    monkeypatch.setattr(turns, "tools_for_chat", lambda *_: [tool])
    monkeypatch.setattr(turns, "can_view_chat", lambda *_: True)
    monkeypatch.setattr(turns, "turn_is_valid", lambda *_: True)
    monkeypatch.setattr(turns, "_make_driver", lambda *_: provider)
    monkeypatch.setattr(turns, "_finish_and_hook", no_summary)
    frames = []

    async def emit(frame):
        frames.append(frame)

    async def run():
        with override_settings(
                DCM_AI_POLICY_ENABLED=False, DCM_AI_POLICY_BASE_URL="",
                DCM_AI_POLICY_ENROLLMENT_CREDENTIAL="", DCM_ASSISTANT_CATALOGUE=[entry],
                DCM_ASSISTANT_EXECUTION_POLICY=policy, DCM_ASSISTANT_EXECUTION_PRICING=pricing,
                DCM_ASSISTANT_SHARED_SCOPE=lambda _scope: False,
                DCM_ASSISTANT_LIMITS={"access_recheck_seconds": 3600}):
            return await turns.run_turn(chat, user, "analyze this synthetic dataset", emit)

    async_to_sync(run)()
    assert state["fetched"] is True
    assert observed and all(value == (chat.pk, user.pk) for value in observed)
    assert len(client.requests) >= 2
    if driver == "openai_api":
        uploads = [row["file"] for row in client.files_created]
        uploads.extend(row[1] for row in client.container_files_created)
    else:
        uploads = [row["file"] for row in client.uploaded]
    assert [row[0] for row in uploads] == ["owned.csv", "fresh.csv"]
    assert all(b"foreign-private" not in row[1] for row in uploads)


@pytest.mark.parametrize("driver", ["openai_api", "anthropic_api"])
def test_provider_refreshes_authorized_datasets_after_repeated_app_queries(monkeypatch, driver):
    from dataclasses import replace
    from django_core_micha.assistant.drivers import anthropic_api, openai_api
    user, chat, turn, entry, pricing, state, observed, policy, fresh = _refresh_context(driver)
    _patch_provider_execution_accounting(monkeypatch, driver)
    client = _openai_tool_refresh_client(lookup_calls=2) if driver == "openai_api" else _AnthropicClient()
    if driver == "anthropic_api":
        client.lookup_uses = 2
    handler_calls = []
    def lookup(*_):
        handler_calls.append(True)
        state["fetched"] = True
        return "queried rows", []
    tool = ToolSpec("lookup", "Lookup", "Reads authorized app data", {"type": "object"}, lookup)
    pricing_settings = {"DCM_ASSISTANT_EXECUTION_POLICY": policy,
        "DCM_ASSISTANT_EXECUTION_PRICING": pricing}

    async def prepare_initial():
        with override_settings(**pricing_settings):
            return await prepare_execution(chat, user, turn, entry, authorize=_yes)
    config = async_to_sync(prepare_initial)()
    async def refresh():
        latest = await prepare_execution(chat, user, turn, entry, authorize=lambda: _yes())
        return replace(latest, session=config.session) if latest is not None else None
    provider = openai_api.OpenAIDriver(client) if driver == "openai_api" else anthropic_api.AnthropicDriver(client)

    async def run():
        with override_settings(**pricing_settings):
            try:
                return [event async for event in provider.stream(model="synthetic-model",
                    messages=[{"role": "user", "content": "query and analyze"}], tools=[tool],
                    user=user, chat=chat, execution=config, authorize=_yes, refresh_execution=refresh)]
            finally:
                await provider.finish_execution(config)

    events = async_to_sync(run)()
    assert len(handler_calls) == 2
    assert all(value == (chat.pk, user.pk) for value in observed)
    assert not any("foreign.csv" in json.dumps(event) for event in events)
    assert any(event["type"] == "execution" for event in events)
    if driver == "openai_api":
        assert [row["file"][0] for row in client.files_created] == ["prior-a.csv", "prior-b.csv"]
        assert [row[1][0] for row in client.container_files_created] == ["fresh.csv"]
        assert len(client.container_files_created) == 1
        provider_refs = ["container-1", "file-input-1", "file-input-2", "container-input-1"]
    else:
        assert [row["file"][0] for row in client.uploaded] == ["prior-a.csv", "prior-b.csv", "fresh.csv"]
        assert client.requests[1]["container"] == {"id": "anthropic-container"}
        uploaded_blocks = [block for message in client.requests[1]["messages"]
            if isinstance(message.get("content"), list) for block in message["content"]
            if block.get("type") == "container_upload"]
        assert [block["file_id"] for block in uploaded_blocks] == [
            "anthropic-input-file", "anthropic-input-file-2", "anthropic-input-file-3"]
        provider_refs = ["anthropic-container", "anthropic-input-file", "anthropic-input-file-2",
            "anthropic-input-file-3", "anthropic-output-file"]
    assert not any(any(ref in json.dumps(event) for ref in provider_refs) for event in events)


async def _yes():
    return True


@pytest.mark.parametrize("driver", ["openai_api", "anthropic_api"])
def test_provider_rechecks_dataset_policy_before_reusing_analysis(monkeypatch, driver):
    from dataclasses import replace
    from django_core_micha.assistant.drivers import anthropic_api, openai_api
    user, chat, turn, entry, pricing, state, _observed, policy, _fresh = _refresh_context(driver, "deny")
    _patch_provider_execution_accounting(monkeypatch, driver)
    client = _OpenAIClient() if driver == "openai_api" else _AnthropicClient()
    if driver == "anthropic_api":
        client.lookup_uses = 0
    provider = openai_api.OpenAIDriver(client) if driver == "openai_api" else anthropic_api.AnthropicDriver(client)
    pricing_settings = {"DCM_ASSISTANT_EXECUTION_POLICY": policy,
        "DCM_ASSISTANT_EXECUTION_PRICING": pricing}

    async def prepare_initial():
        with override_settings(**pricing_settings):
            return await prepare_execution(chat, user, turn, entry, authorize=_yes)
    config = async_to_sync(prepare_initial)()

    async def refresh():
        latest = await prepare_execution(chat, user, turn, entry, authorize=_yes)
        return replace(latest, session=config.session) if latest is not None else None

    async def run():
        with override_settings(**pricing_settings):
            try:
                first = [event async for event in provider.stream(model="synthetic-model", messages=[],
                    user=user, chat=chat, execution=config, authorize=_yes, refresh_execution=refresh)]
                assert any(event["type"] == "execution" for event in first)
                state["fetched"] = True
                with pytest.raises(DriverError, match="execution_unavailable"):
                    [event async for event in provider.stream(model="synthetic-model", messages=[],
                        user=user, chat=chat, execution=config, authorize=_yes, refresh_execution=refresh)]
                return len(client.requests)
            finally:
                await provider.finish_execution(config)

    request_count = async_to_sync(run)()
    assert request_count == 1


@pytest.mark.parametrize("driver", ["openai_api", "anthropic_api"])
@pytest.mark.parametrize("scenario", ["deny", "revoke", "cancel"])
def test_provider_refresh_fails_closed_for_denial_revocation_and_cancellation(monkeypatch, driver, scenario):
    from dataclasses import replace
    from django_core_micha.assistant.drivers import anthropic_api, openai_api
    user, chat, turn, entry, pricing, state, observed, policy, _fresh = _refresh_context(driver, scenario)
    _patch_provider_execution_accounting(monkeypatch, driver)
    cancel_event = asyncio.Event()
    client = _openai_tool_refresh_client(lookup_calls=1) if driver == "openai_api" else _AnthropicClient()
    handler_calls = []
    def lookup(*_):
        handler_calls.append(True)
        state["fetched"] = True
        if scenario == "cancel":
            cancel_event.set()
        return "queried rows", []
    tool = ToolSpec("lookup", "Lookup", "Reads authorized app data", {"type": "object"}, lookup)
    pricing_settings = {"DCM_ASSISTANT_EXECUTION_POLICY": policy,
        "DCM_ASSISTANT_EXECUTION_PRICING": pricing}

    async def prepare_initial():
        with override_settings(**pricing_settings):
            return await prepare_execution(chat, user, turn, entry, authorize=_yes)
    config = async_to_sync(prepare_initial)()
    async def refresh():
        latest = await prepare_execution(chat, user, turn, entry, authorize=_yes)
        return replace(latest, session=config.session) if latest is not None else None
    provider = openai_api.OpenAIDriver(client) if driver == "openai_api" else anthropic_api.AnthropicDriver(client)

    async def run():
        with override_settings(**pricing_settings):
            try:
                return [event async for event in provider.stream(model="synthetic-model",
                    messages=[{"role": "user", "content": "query and analyze"}], tools=[tool],
                    user=user, chat=chat, execution=config, authorize=_yes,
                    refresh_execution=refresh, cancel_event=cancel_event)]
            finally:
                await provider.finish_execution(config)

    expected = "cancelled" if scenario == "cancel" else "execution_unavailable"
    with pytest.raises(DriverError, match=expected):
        async_to_sync(run)()
    assert len(handler_calls) == 1
    assert all(value == (chat.pk, user.pk) for value in observed)
    if driver == "openai_api":
        assert len(client.requests) == 1
        assert client.container_files_created == []
    else:
        assert len(client.requests) == 1
        assert [row["file"][0] for row in client.uploaded] == ["prior-a.csv", "prior-b.csv"]


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


def test_anthropic_repeat_stream_without_container_reattaches_uploaded_inputs(monkeypatch):
    from django_core_micha.assistant.drivers import anthropic_api, base
    from django_core_micha.extraction import cost_guard
    client = _AnthropicClient()
    def plain_stream(**kwargs):
        client.requests.append(copy.deepcopy(kwargs))
        message = NS(content=[NS(type="text", text="done")], container=None,
            usage=NS(input_tokens=3, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                server_tool_use=NS(code_execution_requests=0)))
        return _AnthropicStream([NS(type="content_block_delta", index=0,
            delta=NS(type="text_delta", text="done"))], message)
    monkeypatch.setattr(client.messages, "stream", plain_stream)
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
            for _ in range(2):
                [event async for event in driver.stream(model="synthetic-model",
                    messages=[{"role": "user", "content": "analyze"}], user=NS(pk=5), execution=config)]
        finally:
            await driver.finish_execution(config)
    async_to_sync(run)()
    assert client.upload_count == 1
    for request in client.requests:
        uploads = [block for block in request["messages"][-1]["content"] if block.get("type") == "container_upload"]
        assert [block["file_id"] for block in uploads] == ["anthropic-input-file"]


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
