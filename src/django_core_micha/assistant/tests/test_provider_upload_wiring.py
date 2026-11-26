from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.execution import Dataset, ExecutionConfig


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_driver_forwards_authorization_and_stops_first_upload_on_revocation(provider, monkeypatch):
    from django_core_micha.assistant.drivers import anthropic_api, openai_api
    from django_core_micha.extraction import cost_guard

    module = openai_api if provider == "openai" else anthropic_api
    driver_class = openai_api.OpenAIDriver if provider == "openai" else anthropic_api.AnthropicDriver
    upload = AsyncMock()
    client = SimpleNamespace(files=SimpleNamespace(create=upload, delete=AsyncMock()),
                             beta=SimpleNamespace(files=SimpleNamespace(upload=upload)))
    config = ExecutionConfig(provider, "model", "tool", "1g", 90, 3, 1,
                             (Dataset("private.csv", "restricted", b"private", "text/csv"),))
    monkeypatch.setattr(cost_guard, "reserve_user_budget", lambda **_: object())
    original = module._upload_execution_inputs
    forwarded = []
    async def observe(client, config, authorize=None, refresh=None, cancel_event=None):
        forwarded.append(authorize)
        return await original(client, config, authorize, refresh, cancel_event)
    monkeypatch.setattr(module, "_upload_execution_inputs", observe)
    authorize = AsyncMock(side_effect=[True, False])
    async def refresh():
        return config
    async def run():
        with pytest.raises(DriverError, match="access_lost"):
            async for _ in driver_class(client).stream(model="model", messages=[], execution=config,
                                                       authorize=authorize, refresh_execution=refresh):
                pass
    async_to_sync(run)()
    assert forwarded == [authorize]
    assert authorize.await_count == 2
    upload.assert_not_awaited()
