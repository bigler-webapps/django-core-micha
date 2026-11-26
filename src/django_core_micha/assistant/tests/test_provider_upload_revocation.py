from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.execution import Dataset, ExecutionConfig


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_removed_dataset_is_not_uploaded_after_an_earlier_upload(provider):
    from django_core_micha.assistant.drivers import anthropic_api, openai_api

    first = Dataset("first.csv", "allowed", b"safe", "text/csv")
    removed = Dataset("removed.csv", "restricted", b"withdrawn-marker", "text/csv")
    config = ExecutionConfig(provider, "model", "tool", "1g", 90, 3, 1, (first, removed))
    sent = []

    async def upload(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(id="uploaded")

    client = SimpleNamespace(
        files=SimpleNamespace(create=upload, delete=AsyncMock()),
        beta=SimpleNamespace(files=SimpleNamespace(upload=upload)),
    )
    async def refresh():
        return replace(config, datasets=(first,)) if sent else config

    async def authorized():
        return True

    async def run():
        helper = openai_api._upload_execution_inputs if provider == "openai" else anthropic_api._upload_execution_inputs
        return await helper(client, config, authorize=authorized, refresh=refresh)

    uploaded_ids, refreshed = async_to_sync(run)()
    assert uploaded_ids == ["uploaded"]
    assert refreshed.datasets == (first,)
    assert len(sent) == 1
    assert sent[0]["file"] == (first.name, first.content, first.media_type)
    assert all(b"withdrawn-marker" not in repr(request).encode() for request in sent)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_authorization_revoked_before_next_dataset_upload(provider):
    from django_core_micha.assistant.drivers import anthropic_api, openai_api

    first = Dataset("first.csv", "allowed", b"safe", "text/csv")
    removed = Dataset("removed.csv", "restricted", b"withdrawn-marker", "text/csv")
    config = ExecutionConfig(provider, "model", "tool", "1g", 90, 3, 1, (first, removed))
    sent = []

    async def upload(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(id="uploaded")

    client = SimpleNamespace(
        files=SimpleNamespace(create=upload, delete=AsyncMock()),
        beta=SimpleNamespace(files=SimpleNamespace(upload=upload)),
    )

    async def refresh():
        return config

    async def authorized():
        return not sent

    async def run():
        helper = openai_api._upload_execution_inputs if provider == "openai" else anthropic_api._upload_execution_inputs
        with pytest.raises(DriverError, match="access_lost"):
            await helper(client, config, authorize=authorized, refresh=refresh)

    async_to_sync(run)()
    assert len(sent) == 1
    assert sent[0]["file"] == (first.name, first.content, first.media_type)
    assert all(b"withdrawn-marker" not in repr(request).encode() for request in sent)
