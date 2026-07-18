import pytest
from django.contrib.auth import get_user_model

from django_core_micha.notifications import dispatch as dispatch_module
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import NotificationDelivery, NotificationPreference
from django_core_micha.notifications.types import NotificationType, _REGISTRY, register_notification_type


@pytest.fixture(autouse=True)
def isolated_registry():
    original = _REGISTRY.copy()
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)


def test_dispatcher_registry_contains_singletons_for_all_known_channels():
    expected_types = {
        "chip": dispatch_module.ChipDispatcher,
        "email": dispatch_module.EmailDispatcher,
        "push": dispatch_module.PushDispatcher,
        "todo": dispatch_module.TodoDispatcher,
        "popup": dispatch_module.PopupDispatcher,
    }
    for channel, expected_type in expected_types.items():
        dispatcher = dispatch_module.get_dispatcher(channel)

        assert dispatcher is not None
        assert isinstance(dispatcher, expected_type)
        assert dispatcher.channel == channel
        assert dispatch_module.get_dispatcher(channel) is dispatcher

    assert dispatch_module.get_dispatcher("unknown") is None


def test_retryable_delivery_is_retried_but_permanent_failure_is_not(monkeypatch):
    calls = []

    class RetryableDispatcher:
        channel = "retryable"

        def deliver(self, notification, recipient, ctx=None):
            calls.append("retryable")
            if len(calls) == 1:
                return dispatch_module.DeliveryResult(False, "temporary", retryable=True)
            return dispatch_module.DeliveryResult(True)

    class PermanentFailureDispatcher:
        channel = "permanent"

        def deliver(self, notification, recipient, ctx=None):
            calls.append("permanent")
            return dispatch_module.DeliveryResult(False, "permanent", retryable=False)

    notification = type("Notification", (), {"pk": 1})()
    recipient = object()
    monkeypatch.setitem(dispatch_module._DISPATCHERS, "retryable", RetryableDispatcher())
    monkeypatch.setitem(dispatch_module._DISPATCHERS, "permanent", PermanentFailureDispatcher())

    assert dispatch_module.dispatch("retryable", notification=notification, recipient=recipient) is True
    assert calls == ["retryable", "retryable"]
    assert dispatch_module.dispatch("permanent", notification=notification, recipient=recipient) is False
    assert calls == ["retryable", "retryable", "permanent"]


@pytest.mark.django_db
def test_dispatch_exception_fails_only_its_channel_and_keeps_sibling_delivery(monkeypatch):
    register_notification_type(
        NotificationType(
            key="dispatch-isolation",
            category="system",
            mode="event",
            resolution="user-done",
            default_channels=["chip", "email"],
            eligible_channels=["chip", "email"],
        )
    )
    user = get_user_model().objects.create_user(
        username="dispatch-isolation",
        email="dispatch-isolation@example.test",
        password="password",
    )
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    email_calls = []

    class ExplodingDispatcher:
        channel = "chip"

        def deliver(self, notification, recipient, ctx=None):
            raise RuntimeError("channel layer unavailable")

    class SuccessfulDispatcher:
        channel = "email"

        def deliver(self, notification, recipient, ctx=None):
            email_calls.append(recipient.pk)
            return dispatch_module.DeliveryResult(True)

    monkeypatch.setitem(dispatch_module._DISPATCHERS, "chip", ExplodingDispatcher())
    monkeypatch.setitem(dispatch_module._DISPATCHERS, "email", SuccessfulDispatcher())

    notification = notify(
        type="dispatch-isolation",
        recipients=user,
        content={"title_key": "Title", "body_key": "Body"},
    )

    statuses = dict(
        NotificationDelivery.objects.filter(recipient__notification=notification).values_list("channel", "status")
    )
    assert statuses == {"chip": "failed", "email": "sent"}
    assert len(email_calls) == 1
