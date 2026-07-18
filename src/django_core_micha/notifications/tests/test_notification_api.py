import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from django_core_micha.notifications import dispatch
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
    NotificationPreference,
    PushSubscription,
)
from django_core_micha.notifications.router import resolve_channels
from django_core_micha.notifications.types import (
    NotificationType,
    _REGISTRY,
    get_notification_type,
    register_notification_type,
)


@pytest.fixture(autouse=True)
def isolated_registry():
    original = _REGISTRY.copy()
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)


def make_type(*, key="test_notice", critical=False, defaults=None, eligible=None):
    return NotificationType(
        key=key,
        category="finance",
        mode="event",
        resolution="user-done",
        default_channels=defaults or ["chip", "email", "push"],
        eligible_channels=eligible or ["chip", "email", "push"],
        critical=critical,
    )


def make_user(username):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="password",
    )


def test_render_content_falls_back_to_empty_string_for_missing_keys():
    title, body, link = dispatch._render_content({}, user=None)

    assert title == ""
    assert body == ""
    assert link == ""


def test_notification_type_registry_returns_registered_policy_and_rejects_unknown():
    notification_type = make_type()
    register_notification_type(notification_type)

    assert get_notification_type("test_notice") is notification_type
    with pytest.raises(LookupError):
        get_notification_type("not-registered")


@pytest.mark.django_db
def test_router_override_is_narrowed_to_eligible_channels():
    user = make_user("router-override")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    notification_type = make_type(defaults=["chip", "email"], eligible=["chip", "email"])

    assert resolve_channels(notification_type, user, override=["email", "popup"]) == ["email"]


@pytest.mark.django_db
def test_router_respects_opt_out_but_keeps_chip():
    user = make_user("router-opt-out")
    NotificationPreference.objects.create(user=user, email_opt_in=False)
    notification_type = make_type(defaults=["chip", "email"], eligible=["chip", "email"])

    assert resolve_channels(notification_type, user) == ["chip"]


@pytest.mark.django_db
def test_critical_router_forces_available_default_but_not_unavailable_push():
    user = make_user("router-critical")
    NotificationPreference.objects.create(user=user, email_opt_in=False, push_opt_in=False)
    notification_type = make_type(critical=True, defaults=["email", "push"], eligible=["email", "push"])

    assert resolve_channels(notification_type, user) == ["email"]


@pytest.mark.django_db
def test_notify_creates_canonical_rows_dispatches_and_deduplicates(monkeypatch):
    register_notification_type(make_type())
    users = [make_user("notify-one"), make_user("notify-two")]
    for user in users:
        NotificationPreference.objects.create(user=user, email_opt_in=True, push_opt_in=True)
        PushSubscription.objects.create(
            user=user,
            endpoint=f"https://push.test/{user.username}",
            p256dh="key",
            auth="auth",
        )

    calls = {"chip": [], "email": [], "push": []}
    monkeypatch.setattr(dispatch, "push_to_users", lambda users, payload: calls["chip"].append((users, payload)))
    monkeypatch.setattr(dispatch, "_send_email", lambda **kwargs: calls["email"].append(kwargs))
    monkeypatch.setattr(dispatch, "_send_push", lambda **kwargs: calls["push"].append(kwargs))
    content = {"title_key": "Notification title", "body_key": "Notification body", "params": {}, "link": "/next"}

    first = notify(type="test_notice", recipients=users, content=content)
    second = notify(type="test_notice", recipients=users, content=content)

    assert second == first
    assert Notification.objects.count() == 1
    assert first.recipients.count() == 2
    assert NotificationDelivery.objects.filter(recipient__notification=first).count() == 6
    assert set(NotificationDelivery.objects.values_list("status", flat=True)) == {"sent"}
    assert {channel: len(channel_calls) for channel, channel_calls in calls.items()} == {
        "chip": 2,
        "email": 2,
        "push": 2,
    }
    assert calls["chip"][0][1] == {"type": "test_notice", "content": content, "notification_id": first.pk}


@pytest.mark.django_db
def test_notify_critical_type_actually_delivers_email_despite_opt_out(monkeypatch):
    register_notification_type(
        make_type(critical=True, defaults=["email"], eligible=["email"])
    )
    user = make_user("critical-force")
    NotificationPreference.objects.create(user=user, email_opt_in=False)
    sent_to = []

    class Message:
        def __init__(self, **kwargs):
            self.to = kwargs["to"]

        def attach_alternative(self, *args):
            return None

        def send(self, fail_silently=False):
            sent_to.extend(self.to)

    from django_core_micha.notifications import delivery

    monkeypatch.setattr(delivery, "EmailMultiAlternatives", Message)

    notification = notify(
        type="test_notice",
        recipients=user,
        content={"title_key": "T", "body_key": "B"},
    )

    assert sent_to == [user.email]
    delivery_row = NotificationDelivery.objects.get(recipient__notification=notification, channel="email")
    assert delivery_row.status == "sent"


@pytest.mark.django_db
def test_notify_recovers_from_dedup_integrity_error(monkeypatch):
    register_notification_type(make_type(defaults=["chip"], eligible=["chip"]))
    user = make_user("integrity-retry")
    existing, _ = Notification.objects.get_or_create_by_dedup(
        notification_type="test_notice",
        category="finance",
        content={"title_key": "Title", "body_key": "Body"},
    )
    original = Notification.objects.get_or_create_by_dedup
    attempts = 0

    def raise_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IntegrityError("concurrent insert")
        return original(**kwargs)

    monkeypatch.setattr(Notification.objects, "get_or_create_by_dedup", raise_once)
    monkeypatch.setattr(dispatch, "push_to_users", lambda users, payload: None)

    notification = notify(
        type="test_notice",
        recipients=user,
        content={"title_key": "Title", "body_key": "Body"},
    )

    assert notification == existing
    assert attempts == 1
    assert Notification.objects.count() == 1


@pytest.mark.django_db
def test_notify_recovers_from_null_delivery_integrity_error(monkeypatch):
    register_notification_type(make_type(defaults=["chip"], eligible=["chip"]))
    user = make_user("delivery-integrity-retry")
    notification, _ = Notification.objects.get_or_create_by_dedup(
        notification_type="test_notice",
        category="finance",
        content={"title_key": "Title", "body_key": "Body"},
    )
    recipient = NotificationRecipient.objects.create(notification=notification, user=user)
    existing = NotificationDelivery.objects.create(
        recipient=recipient,
        channel="chip",
        digest_threshold=None,
    )
    original = NotificationDelivery.objects.get_or_create
    attempts = 0

    def raise_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IntegrityError("concurrent delivery insert")
        return original(**kwargs)

    monkeypatch.setattr(NotificationDelivery.objects, "get_or_create", raise_once)
    monkeypatch.setattr(dispatch, "push_to_users", lambda users, payload: None)

    result = notify(
        type="test_notice",
        recipients=user,
        content={"title_key": "Title", "body_key": "Body"},
    )

    assert result == notification
    assert attempts == 1
    assert NotificationDelivery.objects.get(pk=existing.pk).status == "pending"
    assert NotificationDelivery.objects.filter(recipient=recipient, channel="chip").count() == 1
