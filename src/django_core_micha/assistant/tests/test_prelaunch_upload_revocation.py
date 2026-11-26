"""Revocation at the last boundary before provider data leaves the process."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.execution import Dataset, ExecutionConfig


def test_cli_revocation_before_launch_never_starts_process(monkeypatch, tmp_path):
    from django_core_micha.assistant.drivers import claude_cli

    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "claude")
    process_factory = AsyncMock()
    driver = claude_cli.ClaudeCliDriver(process_factory=process_factory)

    async def denied():
        return False

    async def run():
        with pytest.raises(DriverError, match="access_lost"):
            async for _ in driver.stream(prompt="private", model="m", tools=[], credential="x",
                                         chat=SimpleNamespace(pk="chat"), authorize=denied):
                pass

    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        async_to_sync(run)()
    process_factory.assert_not_awaited()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_revocation_before_first_upload_sends_no_dataset(provider):
    from django_core_micha.assistant.drivers import anthropic_api, openai_api

    dataset = Dataset("private.csv", "restricted", b"private", "text/csv")
    config = ExecutionConfig(provider, "model", "tool", "1g", 90, 3, 1, (dataset,))
    upload = AsyncMock()
    client = SimpleNamespace(files=SimpleNamespace(create=upload),
                             beta=SimpleNamespace(files=SimpleNamespace(upload=upload)))

    async def denied():
        return False

    async def refresh():
        return config

    async def run():
        helper = openai_api._upload_execution_inputs if provider == "openai" else anthropic_api._upload_execution_inputs
        with pytest.raises(DriverError, match="access_lost"):
            await helper(client, config, authorize=denied, refresh=refresh)

    async_to_sync(run)()
    upload.assert_not_awaited()
