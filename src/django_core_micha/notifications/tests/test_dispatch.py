import pytest
from django.contrib.auth import get_user_model

from django_core_micha.notifications import dispatch as dispatch_module
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import (
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
)
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
def test_transient_context_never_leaks_to_chip_or_popup_wire_payloads(monkeypatch):
    register_notification_type(
        NotificationType(
            key="transient-websocket",
            category="system",
            mode="event",
            resolution="user-done",
            default_channels=["chip", "popup"],
            eligible_channels=["chip", "popup"],
        )
    )
    user = get_user_model().objects.create_user(
        username="transient-websocket", email="transient-websocket@example.test", password="password"
    )
    payloads = []
    monkeypatch.setattr(dispatch_module, "push_to_users", lambda users, payload: payloads.append(payload))
    content = {"title_key": "Title", "body_key": "Body", "params": {"stored": "value"}}

    notification = notify(
        type="transient-websocket",
        recipients=user,
        content=content,
        transient={"excerpt": "confidential excerpt"},
    )

    assert {payload["channel"] for payload in payloads} == {"chip", "popup"}
    assert all(payload["content"] == content for payload in payloads)
    assert all("confidential excerpt" not in str(payload) for payload in payloads)
    assert notification.content == content


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


@pytest.mark.django_db
def test_popup_dispatcher_delivers_with_channel_discriminator_on_the_wire(monkeypatch):
    register_notification_type(
        NotificationType(
            key="popup-scaffolding-e2e",
            category="system",
            mode="event",
            resolution="user-done",
            default_channels=["popup"],
            eligible_channels=["popup"],
        )
    )
    user = get_user_model().objects.create_user(
        username="popup-e2e",
        email="popup-e2e@example.test",
        password="password",
    )
    sent = []
    monkeypatch.setattr(
        dispatch_module, "push_to_users", lambda users, payload: sent.append((list(users), payload))
    )

    notification = notify(
        type="popup-scaffolding-e2e",
        recipients=user,
        content={"title_key": "Title", "body_key": "Body"},
    )

    statuses = dict(
        NotificationDelivery.objects.filter(recipient__notification=notification).values_list("channel", "status")
    )
    assert statuses == {"popup": "sent"}
    assert len(sent) == 1
    delivered_users, payload = sent[0]
    assert [u.pk for u in delivered_users] == [user.pk]
    assert payload["envelope"] == "notification"
    assert payload["channel"] == "popup"
    assert payload["notification_id"] == notification.pk
    recipient = NotificationRecipient.objects.get(notification=notification, user=user)
    # feed/mark/ (CanonicalMarkView) resolves ids against NotificationRecipient,
    # not Notification — the wire payload must carry the recipient pk, or every
    # markSeen/markDismissed a client sends for this push either no-ops or (worse)
    # mutates an unrelated NotificationRecipient row that shares the same pk.
    assert payload["recipient_id"] == recipient.pk


@pytest.mark.django_db
def test_chip_dispatcher_carries_channel_field_with_no_other_regression(monkeypatch):
    register_notification_type(
        NotificationType(
            key="chip-channel-field",
            category="system",
            mode="event",
            resolution="user-done",
            default_channels=["chip"],
            eligible_channels=["chip"],
        )
    )
    user = get_user_model().objects.create_user(
        username="chip-channel",
        email="chip-channel@example.test",
        password="password",
    )
    sent = []
    monkeypatch.setattr(
        dispatch_module, "push_to_users", lambda users, payload: sent.append(payload)
    )

    notification = notify(
        type="chip-channel-field",
        recipients=user,
        content={"title_key": "Title", "body_key": "Body"},
    )

    assert len(sent) == 1
    payload = sent[0]
    assert payload["envelope"] == "notification"
    assert payload["channel"] == "chip"
    assert payload["type"] == "chip-channel-field"
    assert payload["notification_id"] == notification.pk
    assert payload["content"] == notification.content
    recipient = NotificationRecipient.objects.get(notification=notification, user=user)
    assert payload["recipient_id"] == recipient.pk


@pytest.mark.django_db
def test_resolve_channels_still_excludes_popup_for_types_that_do_not_declare_it():
    from django_core_micha.notifications.router import resolve_channels

    ntype = NotificationType(
        key="no-popup",
        category="system",
        mode="event",
        resolution="user-done",
        default_channels=["chip", "popup"],
        eligible_channels=["chip"],
    )
    user = get_user_model().objects.create_user(
        username="no-popup", email="no-popup@example.test", password="password"
    )

    effective = resolve_channels(ntype, user)

    assert "popup" not in effective
