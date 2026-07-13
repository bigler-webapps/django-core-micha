import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.notifications import delivery
from django_core_micha.notifications.models import NotificationPreference, PushSubscription
from django_core_micha.notifications.views import NotificationInboxView, PushSubscriptionView


@pytest.mark.django_db
def test_send_push_sends_each_active_subscription(settings, monkeypatch):
    settings.VAPID_PRIVATE_KEY = "private"
    settings.VAPID_CLAIM_EMAIL = "push@example.test"
    user = get_user_model().objects.create_user(username="push", email="push@example.test", password="password")
    NotificationPreference.objects.create(user=user, push_opt_in=True)
    first = PushSubscription.objects.create(user=user, endpoint="https://push.test/one", p256dh="one", auth="one")
    second = PushSubscription.objects.create(user=user, endpoint="https://push.test/two", p256dh="two", auth="two")
    sent = []
    monkeypatch.setattr(delivery, "webpush", lambda **kwargs: sent.append(kwargs))

    delivery._send_push(title="Title", body="Body", url="/next", users=[user])

    assert {call["subscription_info"]["endpoint"] for call in sent} == {first.endpoint, second.endpoint}
    assert json.loads(sent[0]["data"]) == {"title": "Title", "body": "Body", "url": "/next"}


@pytest.mark.django_db
def test_send_push_deletes_gone_subscription(settings, monkeypatch):
    settings.VAPID_PRIVATE_KEY = "private"
    user = get_user_model().objects.create_user(username="gone", email="gone@example.test", password="password")
    NotificationPreference.objects.create(user=user, push_opt_in=True)
    subscription = PushSubscription.objects.create(user=user, endpoint="https://push.test/gone", p256dh="key", auth="auth")

    class Gone(Exception):
        response = type("Response", (), {"status_code": 410})()

    monkeypatch.setattr(delivery, "WebPushException", Gone)
    monkeypatch.setattr(delivery, "webpush", lambda **kwargs: (_ for _ in ()).throw(Gone()))
    delivery._send_push(title="Title", body="Body", url="/next", users=[user])

    assert not PushSubscription.objects.filter(pk=subscription.pk).exists()


@pytest.mark.django_db
def test_send_email_filters_opt_in_and_isolates_failures(monkeypatch):
    user_model = get_user_model()
    failing = user_model.objects.create_user(username="failing", email="failing@example.test", password="password")
    succeeding = user_model.objects.create_user(username="succeeding", email="succeeding@example.test", password="password")
    opted_out = user_model.objects.create_user(username="out", email="out@example.test", password="password")
    NotificationPreference.objects.create(user=failing, email_opt_in=True)
    NotificationPreference.objects.create(user=succeeding, email_opt_in=True)
    NotificationPreference.objects.create(user=opted_out, email_opt_in=False)
    sent_to = []

    class Message:
        def __init__(self, **kwargs):
            self.to = kwargs["to"]

        def attach_alternative(self, *args):
            return None

        def send(self, fail_silently=False):
            if self.to == [failing.email]:
                raise RuntimeError("SMTP failure")
            sent_to.extend(self.to)

    monkeypatch.setattr(delivery, "EmailMultiAlternatives", Message)
    delivery._send_email(title="Title", body="Body", url="/next", users=[failing, succeeding, opted_out])

    assert sent_to == [succeeding.email]


def test_push_to_users_targets_only_each_users_group(monkeypatch):
    recorded = []

    class Layer:
        async def group_send(self, group, event):
            recorded.append((group, event))

    layer = Layer()
    monkeypatch.setattr("channels.layers.get_channel_layer", lambda: layer)
    monkeypatch.setattr("asgiref.sync.async_to_sync", lambda fn: lambda *args, **kwargs: recorded.append((args[0], args[1])))
    users = [type("User", (), {"id": 1})(), type("User", (), {"id": 2})()]

    delivery.push_to_users(users, {"kind": "update"})

    assert recorded == [
        ("notifications_user_1", {"type": "message", "payload": {"kind": "update"}}),
        ("notifications_user_2", {"type": "message", "payload": {"kind": "update"}}),
    ]


@pytest.mark.django_db
def test_push_subscription_cannot_be_claimed_by_another_user():
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="owner", email="owner@example.test", password="password")
    other = user_model.objects.create_user(username="other", email="other@example.test", password="password")
    PushSubscription.objects.create(user=owner, endpoint="https://push.test/owned", p256dh="key", auth="auth")
    request = APIRequestFactory().post(
        "/notifications/preferences/push-subscription/",
        {"subscription": {"endpoint": "https://push.test/owned", "keys": {"p256dh": "new", "auth": "new"}}},
        format="json",
    )
    force_authenticate(request, user=other)

    response = PushSubscriptionView.as_view()(request)

    assert response.status_code == 409
    assert PushSubscription.objects.get(endpoint="https://push.test/owned").user_id == owner.id


def test_optional_inbox_returns_501_when_notification_model_is_unset(settings):
    settings.NOTIFICATION_MODEL = ""
    request = APIRequestFactory().get("/notifications/inbox/")
    force_authenticate(request, user=type("User", (), {"is_authenticated": True})())

    response = NotificationInboxView.as_view()(request)

    assert response.status_code == 501
