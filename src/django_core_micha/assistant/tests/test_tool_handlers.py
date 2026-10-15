from types import SimpleNamespace as NS

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model

from django_core_micha.assistant.drivers.base import DriverError, invoke_tool

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="tool-user", password="secret")


@pytest.mark.django_db(transaction=True)
def test_sync_handler_database_read_returns_content_and_sources(user):
    tool = NS(handler=lambda current_user, chat, arguments, trust: (
        get_user_model().objects.get(pk=current_user.pk).username,
        [{"label": "Account", "reference": str(current_user.pk)}],
    ))
    assert async_to_sync(invoke_tool)(tool, user, NS(pk="chat"), {}, "cloud") == (
        user.username,
        [{"label": "Account", "reference": str(user.pk)}],
    )


def test_async_handler_is_awaited_unchanged():
    async def handler(user, chat, arguments, trust_class):
        return f"answer:{arguments['q']}:{trust_class}", [{"label": "Doc", "reference": "r1"}]

    assert async_to_sync(invoke_tool)(NS(handler=handler), None, None, {"q": "x"}, "cloud") == (
        "answer:x:cloud", [{"label": "Doc", "reference": "r1"}]
    )


def test_sync_handler_exception_maps_to_provider_error():
    def handler(*args):
        raise RuntimeError("internal detail")

    with pytest.raises(DriverError) as error:
        async_to_sync(invoke_tool)(NS(handler=handler), None, None, {}, "cloud")
    assert error.value.code == "provider_error"


def test_sync_handler_runs_exactly_once():
    calls = []

    def handler(*args):
        calls.append(args)
        return "ok", []

    assert async_to_sync(invoke_tool)(NS(handler=handler), "u", "c", {"x": 1}, "cloud") == ("ok", [])
    assert calls == [("u", "c", {"x": 1}, "cloud")]
