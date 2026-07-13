import pytest

from django_core_micha.auth.ws_permissions import assert_all_consumers_secure
from django_core_micha.notifications.consumers import NotificationConsumer


def test_notification_consumers_are_secure():
    assert assert_all_consumers_secure(["django_core_micha.notifications.consumers"]) == []


@pytest.mark.asyncio
async def test_unauthenticated_connect_is_rejected():
    consumer = NotificationConsumer.__new__(NotificationConsumer)
    consumer.scope = {"user": type("Anon", (), {"is_authenticated": False})()}
    closed = []

    async def close(code):
        closed.append(code)

    consumer.close = close
    await consumer.connect()
    assert closed == [4401]


@pytest.mark.asyncio
async def test_authenticated_connect_joins_user_group():
    consumer = NotificationConsumer.__new__(NotificationConsumer)
    consumer.scope = {"user": type("User", (), {"id": 42, "is_authenticated": True})()}
    calls = []

    class Layer:
        async def group_add(self, group, channel):
            calls.append((group, channel))

    consumer.channel_layer = Layer()
    consumer.channel_name = "channel-1"

    async def accept():
        return None

    consumer.accept = accept
    consumer.close = lambda code: None
    await consumer.connect()
    assert calls == [("notifications_user_42", "channel-1")]
