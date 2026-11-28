import json
from datetime import timedelta
from hashlib import sha256

import pytest
from django.contrib.auth import get_user_model
from django.middleware.csrf import _get_new_csrf_string, _mask_cipher_secret
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.notifications import dispatch as dispatch_module
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.delivery import _push_payload
from django_core_micha.notifications.models import (
    Notification, NotificationDelivery, NotificationPushActionToken, NotificationRecipient, PushSubscription,
)
from django_core_micha.notifications.text_registry import _REGISTRY as TEXT_REGISTRY, register_notification_text
from django_core_micha.notifications.types import NotificationType, _REGISTRY as TYPE_REGISTRY, register_notification_type
from django_core_micha.notifications.views import PushCsrfView, PushDoneView


@pytest.fixture(autouse=True)
def isolated_registries():
    types, texts = TYPE_REGISTRY.copy(), TEXT_REGISTRY.copy()
    TYPE_REGISTRY.clear()
    TEXT_REGISTRY.clear()
    yield
    TYPE_REGISTRY.clear()
    TYPE_REGISTRY.update(types)
    TEXT_REGISTRY.clear()
    TEXT_REGISTRY.update(texts)


def user(name):
    return get_user_model().objects.create_user(username=name, email=f"{name}@example.test", password="password")


def test_plain_push_payload_is_unchanged_without_options():
    assert json.loads(_push_payload(title="Title", body="Body", url="/notice/")) == {
        "title": "Title", "body": "Body", "url": "/notice/",
    }


@pytest.mark.django_db
def test_renotify_requires_effective_tag():
    register_notification_type(NotificationType(key="bad", category="system", mode="event", resolution="user-done", active=True, push_renotify=True))
    with pytest.raises(ValueError, match="requires a nonempty"):
        notify(type="bad", recipients=user("renotify"), channels=[], content={})
    notify(type="bad", recipients=user("tagged"), channels=[], content={}, push_tag="record")


@pytest.mark.django_db(transaction=True)
@override_settings(PUBLIC_ORIGIN="https://app.example.test")
def test_done_action_issues_only_hashed_token_and_localized_options(monkeypatch, caplog):
    register_notification_text("open", {"de": "Öffnen"})
    register_notification_text("done", {"de": "Erledigt"})
    register_notification_type(NotificationType(
        key="actionable", category="system", mode="event", resolution="user-done", active=True, critical=True,
        push_tag="fixed", push_renotify=True, push_open_label_key="open", push_done_label_key="done",
    ))
    recipient_user = user("actionable")
    PushSubscription.objects.create(
        user=recipient_user, endpoint="https://fcm.googleapis.com/fcm/send/actionable", p256dh="key", auth="auth",
    )
    sent = []
    monkeypatch.setattr(dispatch_module, "_send_push", lambda **kwargs: sent.append(kwargs))

    notification = notify(
        type="actionable", recipients=recipient_user, channels=["push"],
        content={"title_key": "Title", "body_key": "Body", "link": "/default/"},
        push_tag="per-record", push_open_url="https://app.example.test/target/",
    )

    token = NotificationPushActionToken.objects.get()
    assert token.token_hash == sha256(sent[0]["extras"]["done_token"].encode()).hexdigest()
    assert sent[0]["extras"]["tag"] == "per-record"
    assert sent[0]["extras"]["actions"] == [
        {"action": "open", "title": "Öffnen", "url": "https://app.example.test/target/"},
        {"action": "done", "title": "Erledigt"},
    ]
    assert sent[0]["extras"]["done_token"] not in token.token_hash
    assert notification.pk
    cleartext = sent[0]["extras"]["done_token"]
    delivery = NotificationDelivery.objects.get(recipient__user=recipient_user, channel="push")
    assert token.delivery_id == delivery.pk
    assert token.recipient_id == delivery.recipient_id
    assert token.recipient.user_id == recipient_user.pk
    assert token.action == "done"
    assert token.consumed_at is None
    assert timedelta(days=13, hours=23) < token.expires_at - token.created_at <= timedelta(days=14, minutes=1)
    assert len(cleartext) >= 43
    assert cleartext not in token.token_hash
    assert cleartext not in caplog.text
    assert cleartext not in json.dumps(notification.content)


@pytest.mark.django_db
@override_settings(PUBLIC_ORIGIN="https://app.example.test")
def test_done_requires_origin_csrf_and_consumes_once(monkeypatch):
    owner, other = user("owner"), user("other")
    notification = Notification.objects.create(notification_type="done", category="system", content={}, dedup_key="done")
    recipient = NotificationRecipient.objects.create(notification=notification, user=owner)
    delivery = NotificationDelivery.objects.create(recipient=recipient, channel="push")
    cleartext = "x" * 44
    from hashlib import sha256
    from django.utils import timezone
    token = NotificationPushActionToken.objects.create(
        delivery=delivery, recipient=recipient, action="done", token_hash=sha256(cleartext.encode()).hexdigest(),
        expires_at=timezone.now() + timezone.timedelta(days=1),
    )
    monkeypatch.setattr("django_core_micha.notifications.views.push_to_users", lambda *_args: None)
    secret = _get_new_csrf_string()

    def request_for(account, csrf=True):
        request = APIRequestFactory(enforce_csrf_checks=True).post(
            "/api/notifications/push/done/", {"token": cleartext}, format="json",
            HTTP_ORIGIN="https://app.example.test",
            HTTP_X_CSRFTOKEN=_mask_cipher_secret(secret) if csrf else "bad",
        )
        request.COOKIES["csrftoken"] = secret
        force_authenticate(request, user=account)
        return request

    assert PushDoneView.as_view()(request_for(other)).status_code == 403
    assert PushDoneView.as_view()(request_for(owner, csrf=False)).status_code == 403
    assert PushDoneView.as_view()(request_for(owner)).status_code == 403
    csrf_request = APIRequestFactory().get("/api/notifications/push/csrf/")
    force_authenticate(csrf_request, user=owner)
    assert PushCsrfView.as_view()(csrf_request).status_code == 403
    recipient.refresh_from_db()
    token.refresh_from_db()
    assert recipient.done_at is None and token.consumed_at is None
    assert PushDoneView.as_view()(request_for(owner)).status_code == 403


@pytest.mark.parametrize("body", ["é" * 5000, '\\"' * 5000])
def test_payload_budget_counts_serialized_bytes(body):
    extras = {"icon": "/icon", "badge": "/badge", "tag": "tag", "renotify": True, "actions": [{"action": "done", "title": "Done"}], "done_token": "token"}
    result = _push_payload(title="Title", body=body, url="/", extras=extras)
    payload = json.loads(result)
    assert len(result.encode("utf-8")) <= 3800
    assert "actions" not in payload
    assert "done_token" not in payload
    assert payload["title"] == "Title"
    assert payload["url"] == "/"
    assert payload["body"]
    assert body.startswith(payload["body"])


@pytest.mark.django_db
@override_settings(VAPID_PRIVATE_KEY="key", VAPID_CLAIM_EMAIL="ops@example.test")
def test_oversized_title_is_skipped_without_aborting_the_caller(monkeypatch):
    from django_core_micha.notifications import delivery

    account = user("oversized")
    PushSubscription.objects.create(user=account, endpoint="https://fcm.googleapis.com/fcm/send/oversized", p256dh="key", auth="auth")
    calls = []
    monkeypatch.setattr(delivery, "webpush", lambda **kwargs: calls.append(kwargs))
    delivery._send_push(title="T" * 5000, body="Body", url="/", users=[account], bypass_preference_check=True)
    assert calls == []
    delivery._send_push(title="Title", body="Body", url="/", users=[account], bypass_preference_check=True)
    assert len(calls) == 1


def test_payload_never_has_orphan_done_action_or_token():
    action = {"action": "done", "title": "Done"}
    without_token = json.loads(_push_payload(title="T", body="B", url="/", extras={"actions": [action]}))
    without_action = json.loads(_push_payload(title="T", body="B", url="/", extras={"done_token": "token"}))
    assert action not in without_token.get("actions", [])
    assert "done_token" not in without_action


@pytest.mark.django_db
@override_settings(PUBLIC_ORIGIN="https://app.example.test")
@pytest.mark.parametrize("url", ["/\\evil.example", "//evil.example", "https://evil.example/", "/safe\npath"])
def test_open_url_rejects_cross_origin_values(url):
    register_notification_type(NotificationType(key="url", category="system", mode="event", resolution="user-done", active=True))
    with pytest.raises(ValueError, match="same-origin"):
        notify(type="url", recipients=user("url"), channels=[], content={}, push_open_url=url)
