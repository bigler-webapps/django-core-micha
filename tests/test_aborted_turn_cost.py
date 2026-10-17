import asyncio
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings

from django_core_micha.assistant.drivers.anthropic_api import AnthropicDriver
from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.drivers.openai_api import OpenAIDriver

pytestmark = pytest.mark.django_db(transaction=True)


class _Texts:
    def __init__(self, *, fail=None, gate=None):
        self.fail, self.gate = fail, gate

    def __aiter__(self):
        async def values():
            yield "partial"
            if self.gate is not None:
                await self.gate.wait()
            if self.fail:
                raise self.fail
        return values()


class _AnthropicStream:
    def __init__(self, *, fail=None, gate=None):
        self.text_stream = _Texts(fail=fail, gate=gate)

    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return None

    async def get_final_message(self):
        from anthropic.types import Message
        from anthropic.types.usage import Usage
        return Message.model_construct(id="msg_1", type="message", role="assistant", content=[],
            model="claude-sonnet-5", stop_reason="end_turn", stop_sequence=None,
            usage=Usage.model_construct(input_tokens=11, output_tokens=3,
                cache_creation_input_tokens=0, cache_read_input_tokens=0))


class _OpenAIEvents:
    def __init__(self, *, fail=None, gate=None): self.fail, self.gate = fail, gate

    def __aiter__(self):
        async def events():
            from openai.types.responses import ResponseCompletedEvent, Response, ResponseUsage
            # Output text event is a real SDK object too.
            from openai.types.responses import ResponseTextDeltaEvent
            yield ResponseTextDeltaEvent.model_construct(type="response.output_text.delta", delta="partial")
            if self.gate is not None:
                await self.gate.wait()
            if self.fail:
                raise self.fail
            yield ResponseCompletedEvent.model_construct(type="response.completed",
                response=Response.model_construct(usage=ResponseUsage.model_construct(
                    input_tokens=11, output_tokens=3, input_tokens_details=None), output=[]))
        return events()


class _Client:
    def __init__(self, provider, *, fail=None, gate=None):
        self.provider, self.fail, self.gate = provider, fail, gate
        self.kwargs = None
        self.messages = self
        self.responses = self

    def stream(self, **kwargs):
        self.kwargs = kwargs
        if self.fail: raise self.fail
        return _AnthropicStream(fail=None, gate=self.gate)

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.fail: raise self.fail
        return _OpenAIEvents(fail=None, gate=self.gate)


def _key(user_id):
    from django_core_micha.extraction.cost_guard import _cache_key, _reservation_prefix, _reservation_buckets
    from django_core_micha.extraction import cost_guard
    daykey = _cache_key(user_id)
    day = daykey.rsplit(":", 1)[-1]
    buckets = _reservation_buckets(cost_guard.time.time(), cost_guard._reservation_ttl_seconds())
    reserved = sum(int(cache.get(f"{_reservation_prefix(user_id, day)}:{bucket}", 0) or 0) for bucket in buckets)
    return int(cache.get(daykey, 0) or 0), reserved


def _driver(provider, client):
    return AnthropicDriver(client) if provider == "anthropic_api" else OpenAIDriver(client)


@pytest.fixture
def user():
    cache.clear()
    return get_user_model().objects.create_user(username="aborted-cost-user", password="secret")


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
def test_cancel_after_delta_settles_reserved_cost_and_records_aborted_turn(provider, user):
    async def run():
        gate = asyncio.Event()
        driver = _driver(provider, _Client(provider, gate=gate))
        async def consume():
            async for _ in driver.stream(model="gpt-4o", messages=[{"role": "user", "content": "hello"}], user=user):
                await asyncio.sleep(0)
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1000):
        async_to_sync(run)()
    spent, reserved = _key(user.pk)
    assert spent > 0 and reserved == 0
    from django_core_micha.billing.models import AICostRecord
    record = AICostRecord.objects.get(user=user, purpose="chat_aborted")
    expected_input = max(1, (len('[{"role": "user", "content": "hello"}]'.encode()) + 1) // 2)
    from django_core_micha.extraction.cost_guard import estimate_cost_cents
    assert record.provider == provider and record.input_tokens == expected_input and record.output_tokens == 8192
    assert spent == estimate_cost_cents(model="gpt-4o", input_tokens=expected_input, output_tokens=8192)


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
def test_generator_close_charges_but_pre_call_and_client_rejection_release(provider, user):
    from django_core_micha.billing.models import AICostRecord
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1000):
        async def close_after_delta():
            gen = _driver(provider, _Client(provider)).stream(model="gpt-4o", messages=[], user=user)
            assert (await gen.__anext__())["type"] == "delta"
            await gen.aclose()
        async_to_sync(close_after_delta)()
        spent, reserved = _key(user.pk)
        assert spent > 0 and reserved == 0
        assert AICostRecord.objects.filter(user=user, purpose="chat_aborted").count() == 1

        cache.clear()
        class BadStatus(Exception):
            status_code = 400
        async def rejected():
            async for _ in _driver(provider, _Client(provider, fail=BadStatus())).stream(
                    model="gpt-4o", messages=[], user=user): pass
        with pytest.raises(DriverError) as rejected_error: async_to_sync(rejected)()
        assert rejected_error.value.code == "provider_error" and type(rejected_error.value.__cause__) is BadStatus
        assert _key(user.pk) == (0, 0)
        assert AICostRecord.objects.filter(user=user, purpose="chat_aborted").count() == 1


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
@pytest.mark.parametrize("status", [429, 500])
def test_retryable_and_server_errors_charge(provider, status, user):
    class StatusError(Exception):
        def __init__(self, code): self.status_code = code
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1000):
        async def fail_midstream():
            client = _Client(provider)
            if provider == "anthropic_api":
                class Messages(_Client):
                    def stream(self, **_kwargs): return _AnthropicStream(fail=StatusError(status))
                client.messages = Messages(provider)
            else:
                class Responses(_Client):
                    async def create(self, **_kwargs): return _OpenAIEvents(fail=StatusError(status))
                client.responses = Responses(provider)
            async for _ in _driver(provider, client).stream(model="gpt-4o", messages=[], user=user): pass
        with pytest.raises(DriverError) as status_error: async_to_sync(fail_midstream)()
        assert status_error.value.code == ("quota_exceeded" if status == 429 else "provider_error") and type(status_error.value.__cause__) is StatusError
        spent, reserved = _key(user.pk)
        assert spent > 0 and reserved == 0
        from django_core_micha.billing.models import AICostRecord
        assert AICostRecord.objects.filter(user=user, purpose="chat_aborted").count() == 1


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
@pytest.mark.parametrize("failure", [asyncio.TimeoutError("timed out"), ConnectionError("disconnected")])
def test_timeout_and_connection_loss_charge_and_preserve_exception(provider, failure, user):
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1000):
        async def fail_midstream():
            client = _Client(provider)
            if provider == "anthropic_api":
                class Messages:
                    def stream(self, **_kwargs): return _AnthropicStream(fail=failure)
                client.messages = Messages()
            else:
                class Responses:
                    async def create(self, **_kwargs): return _OpenAIEvents(fail=failure)
                client.responses = Responses()
            async for _ in _driver(provider, client).stream(model="gpt-4o", messages=[], user=user): pass
        with pytest.raises(DriverError) as caught:
            async_to_sync(fail_midstream)()
        assert caught.value.code == "provider_error" and caught.value.__cause__ is failure  # the original stays the cause
        assert _key(user.pk)[0] > 0 and _key(user.pk)[1] == 0


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
def test_quota_refusal_before_provider_call_is_free(provider, user):
    client = _Client(provider)
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1):
        async def denied():
            async for _ in _driver(provider, client).stream(model="gpt-4o", messages=[], user=user): pass
        from django_core_micha.assistant.drivers.base import DriverError
        with pytest.raises(DriverError) as caught: async_to_sync(denied)()
        assert caught.value.code == "quota_exceeded"
        assert client.kwargs is None
        assert _key(user.pk) == (0, 0)
        from django_core_micha.billing.models import AICostRecord
        assert not AICostRecord.objects.filter(user=user).exists()


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
def test_aborted_charge_without_billing_and_failed_record_keep_original(provider, monkeypatch, user):
    from django.apps import apps
    import django_core_micha.billing.services as billing_services
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1000):
        monkeypatch.setattr(apps, "is_installed", lambda _name: False)
        async def timeout_without_billing():
            failure = asyncio.TimeoutError("provider timeout")
            client = _Client(provider)
            if provider == "anthropic_api":
                class Messages:
                    def stream(self, **_kwargs): return _AnthropicStream(fail=failure)
                client.messages = Messages()
            else:
                class Responses:
                    async def create(self, **_kwargs): return _OpenAIEvents(fail=failure)
                client.responses = Responses()
            async for _ in _driver(provider, client).stream(model="gpt-4o", messages=[], user=user): pass
        with pytest.raises(DriverError) as timeout_error: async_to_sync(timeout_without_billing)()
        assert isinstance(timeout_error.value.__cause__, asyncio.TimeoutError)
        assert _key(user.pk)[0] > 0 and _key(user.pk)[1] == 0
        monkeypatch.setattr(apps, "is_installed", lambda _name: True)
        record_attempts = []
        def failing_record(**_kwargs):
            record_attempts.append(1)
            raise RuntimeError("db")
        monkeypatch.setattr(billing_services, "record_ai_call", failing_record)
        failure = ConnectionError("original")
        async def record_failure():
            client = _Client(provider)
            if provider == "anthropic_api":
                class Messages:
                    def stream(self, **_kwargs): return _AnthropicStream(fail=failure)
                client.messages = Messages()
            else:
                class Responses:
                    async def create(self, **_kwargs): return _OpenAIEvents(fail=failure)
                client.responses = Responses()
            async for _ in _driver(provider, client).stream(model="gpt-4o", messages=[], user=user): pass
        with pytest.raises(DriverError) as caught: async_to_sync(record_failure)()
        assert caught.value.__cause__ is failure  # a failing record write never replaces the provider failure
        assert record_attempts == [1] and _key(user.pk)[0] > 0  # the write was attempted, the cache charge still happened


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
def test_aborted_turns_exhaust_daily_cap(provider, user):
    from django_core_micha.assistant.drivers.base import DriverError
    from django_core_micha.billing.models import AICostRecord
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=10):
        async def stop_once():
            gen = _driver(provider, _Client(provider)).stream(model="gpt-4o", messages=[], user=user)
            assert (await gen.__anext__())["type"] == "delta"
            await gen.aclose()
        async_to_sync(stop_once)()
        async def next_turn():
            async for _ in _driver(provider, _Client(provider)).stream(model="gpt-4o", messages=[], user=user): pass
        with pytest.raises(DriverError) as caught: async_to_sync(next_turn)()
        assert caught.value.code == "quota_exceeded"
        assert _key(user.pk)[0] >= 1 and _key(user.pk)[1] == 0
        assert AICostRecord.objects.filter(user=user, purpose="chat_aborted").count() == 1


@pytest.mark.parametrize("provider", ["anthropic_api", "openai_api"])
def test_normal_completion_uses_measured_usage_and_anthropic_max_tokens(provider, user):
    client = _Client(provider)
    driver = _driver(provider, client)
    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1000):
        async def finish():
            return [event async for event in driver.stream(model="gpt-4o", messages=[], user=user,
                **({"max_tokens": 100} if provider == "anthropic_api" else {}))]
        events = async_to_sync(finish)()
    usage = [event for event in events if event["type"] == "usage"]
    assert len(usage) == 1 and usage[0]["usage"]["input_tokens"] == 11
    assert client.kwargs.get("max_tokens") == 100 if provider == "anthropic_api" else "max_output_tokens" not in client.kwargs
    from django_core_micha.billing.models import AICostRecord
    assert not AICostRecord.objects.filter(user=user, purpose="chat_aborted").exists()
    from django_core_micha.extraction.cost_guard import estimate_cost_cents
    actual_cost = estimate_cost_cents(model="gpt-4o", input_tokens=11, output_tokens=3)
    assert _key(user.pk) == (actual_cost, 0)
