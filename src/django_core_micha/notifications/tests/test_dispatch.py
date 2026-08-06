import logging

import pytest
from django.contrib.auth import get_user_model

from django_core_micha.messaging.notification_texts import (
    NEW_MESSAGE_BODY_HIDDEN_KEY,
    NEW_MESSAGE_BODY_KEY_BY_KIND,
    NEW_MESSAGE_TITLE_KEY,
    NEW_MESSAGE_TITLE_UNKNOWN_SENDER_KEY,
    register_messaging_notification_texts,
)
from django_core_micha.notifications import dispatch as dispatch_module
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
)
from django_core_micha.notifications.text_registry import SUPPORTED_LANGUAGES
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
    # NOTIF-26: passive reach is atomic (chip only) -- a type can no longer declare
    # "popup" directly. This drives both dispatchers straight, bypassing notify()'s
    # reach resolution entirely, since the leak-prevention behaviour under test lives
    # in the dispatchers themselves, not in routing.
    user = get_user_model().objects.create_user(
        username="transient-websocket", email="transient-websocket@example.test", password="password"
    )
    content = {"title_key": "Title", "body_key": "Body", "params": {"stored": "value"}}
    notification, _ = Notification.objects.get_or_create_by_dedup(
        notification_type="transient-websocket", category="system", content=content,
    )
    recipient = NotificationRecipient.objects.create(notification=notification, user=user)
    payloads = []
    monkeypatch.setattr(dispatch_module, "push_to_users", lambda users, payload: payloads.append(payload))

    for channel in ("chip", "popup"):
        dispatch_module.dispatch(channel, notification=notification, recipient=recipient, ctx={"excerpt": "confidential excerpt"})

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
            active=True,
            passive=True,
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
    # NOTIF-26: no type can declare "popup" via reach anymore (passive is atomic --
    # chip only). This dispatches straight to the PopupDispatcher, bypassing notify()'s
    # reach resolution, since the scaffolding under test is the dispatcher's wire
    # format, not routing.
    user = get_user_model().objects.create_user(
        username="popup-e2e",
        email="popup-e2e@example.test",
        password="password",
    )
    notification, _ = Notification.objects.get_or_create_by_dedup(
        notification_type="popup-scaffolding-e2e", category="system",
        content={"title_key": "Title", "body_key": "Body"},
    )
    recipient = NotificationRecipient.objects.create(notification=notification, user=user)
    sent = []
    monkeypatch.setattr(
        dispatch_module, "push_to_users", lambda users, payload: sent.append((list(users), payload))
    )

    result = dispatch_module.dispatch("popup", notification=notification, recipient=recipient)

    assert result is True
    assert len(sent) == 1
    delivered_users, payload = sent[0]
    assert [u.pk for u in delivered_users] == [user.pk]
    assert payload["envelope"] == "notification"
    assert payload["channel"] == "popup"
    assert payload["notification_id"] == notification.pk
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
            active=False,
            passive=True,
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

    # NOTIF-26: passive reach is atomic (chip only) -- popup is structurally
    # unreachable via any reach declaration, not merely excluded by a narrower
    # eligible list, per the WO's explicit "no currently-registered type uses popup"
    # basis for making the axis atomic.
    ntype = NotificationType(
        key="no-popup",
        category="system",
        mode="event",
        resolution="user-done",
        active=True,
        passive=True,
    )
    user = get_user_model().objects.create_user(
        username="no-popup", email="no-popup@example.test", password="password"
    )

    assert "popup" not in ntype.eligible_channels
    assert "popup" not in resolve_channels(ntype, user)


class _FakeUser:
    """Language resolution only needs `.contact_profile` to raise AttributeError."""


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize(
    "key",
    [
        NEW_MESSAGE_TITLE_KEY,
        NEW_MESSAGE_TITLE_UNKNOWN_SENDER_KEY,
        NEW_MESSAGE_BODY_HIDDEN_KEY,
        *NEW_MESSAGE_BODY_KEY_BY_KIND.values(),
    ],
)
def test_every_messaging_notification_key_renders_to_a_non_key_string(key, language):
    register_messaging_notification_texts()

    rendered = dispatch_module._resolve_text_key(
        key, language, {"sender": "Alex", "excerpt": "hi there"}, kind="body", require_registered=True
    )

    assert rendered != key
    assert rendered


def test_missing_catalogue_entry_logs_a_warning_naming_the_key(caplog):
    with caplog.at_level(logging.WARNING, logger=dispatch_module.__name__):
        rendered = dispatch_module._resolve_text_key(
            "messaging.does_not_exist", "de", {}, kind="body", require_registered=True
        )

    assert rendered == "messaging.does_not_exist"
    assert any("messaging.does_not_exist" in record.getMessage() for record in caplog.records)


def test_missing_catalogue_entry_is_silent_for_a_caller_that_never_opted_into_the_registry(caplog):
    # Every non-messaging notification type today authors its own literal title/body
    # text directly as `title_key`/`body_key` -- see test_notification_api.py -- and
    # must keep working with no registry involved and no warning noise.
    with caplog.at_level(logging.WARNING, logger=dispatch_module.__name__):
        rendered = dispatch_module._resolve_text_key("Static Title", "de", {}, kind="title")

    assert rendered == "Static Title"
    assert caplog.records == []


def test_render_content_no_longer_returns_the_raw_key_for_a_registered_template():
    # Regression test for MSG-13: pre-fix, `_render_content` called `gettext(title_key)`
    # against a repo shipping no `.po` catalogue at all, so it returned the key verbatim
    # with no exception -- this must fail against that code.
    register_messaging_notification_texts()

    title, body, _ = dispatch_module._render_content(
        {
            "title_key": NEW_MESSAGE_TITLE_KEY,
            "body_key": NEW_MESSAGE_BODY_KEY_BY_KIND["chat"],
            "require_registered_text": True,
            "params": {},
        },
        user=_FakeUser(),
        transient={"sender": "Jamie Lee", "excerpt": "see you tomorrow"},
    )

    assert title == "Jamie Lee"
    assert body == "see you tomorrow"
    assert title != NEW_MESSAGE_TITLE_KEY
    assert body != NEW_MESSAGE_BODY_KEY_BY_KIND["chat"]


@pytest.mark.django_db
def test_push_preview_enabled_defaults_true_with_no_preference_row():
    user = get_user_model().objects.create_user(
        username="preview-no-row", email="preview-no-row@example.test", password="password"
    )

    assert dispatch_module._push_preview_enabled(user) is True


@pytest.mark.django_db
def test_push_preview_enabled_reads_the_stored_value():
    user = get_user_model().objects.create_user(
        username="preview-row", email="preview-row@example.test", password="password"
    )
    NotificationPreference.objects.create(user=user, push_preview_opt_in=False)

    assert dispatch_module._push_preview_enabled(user) is False


@pytest.mark.django_db
def test_notificationpreference_push_preview_opt_in_defaults_true():
    user = get_user_model().objects.create_user(
        username="prefs-default", email="prefs-default@example.test", password="password"
    )

    preference = NotificationPreference.objects.create(user=user)

    assert preference.push_preview_opt_in is True


@pytest.mark.django_db
def test_push_dispatcher_hides_preview_body_but_keeps_the_sender_name_in_the_title(monkeypatch):
    register_notification_type(
        NotificationType(
            key="preview-off-e2e",
            category="system",
            mode="event",
            resolution="user-done",
            active=True,
            passive=False,
        )
    )
    register_messaging_notification_texts()
    user = get_user_model().objects.create_user(
        username="preview-off-e2e", email="preview-off-e2e@example.test", password="password"
    )
    NotificationPreference.objects.create(user=user, push_opt_in=True, push_preview_opt_in=False)
    sent = []
    monkeypatch.setattr(dispatch_module, "_send_push", lambda **kwargs: sent.append(kwargs))

    notify(
        type="preview-off-e2e",
        recipients=user,
        content={
            "title_key": NEW_MESSAGE_TITLE_KEY,
            "body_key": NEW_MESSAGE_BODY_KEY_BY_KIND["chat"],
            "hidden_body_key": NEW_MESSAGE_BODY_HIDDEN_KEY,
            "params": {},
        },
        transient={"sender": "Jamie Lee", "excerpt": "sensitive body text"},
    )

    assert len(sent) == 1
    assert sent[0]["title"] == "Jamie Lee"
    assert "sensitive body text" not in sent[0]["body"]
    assert "Jamie Lee" not in sent[0]["body"]
