import json
from datetime import timedelta
from hashlib import sha256

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone

from django_core_micha.notifications import dispatch
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import Notification, NotificationDelivery, NotificationPushActionToken, NotificationRecipient, PushSubscription
from django_core_micha.notifications.types import NotificationType, _REGISTRY, register_notification_type


ORIGIN = "http://testserver"
CSRF_URL = "/api/notifications/push/csrf/"
DONE_URL = "/api/notifications/push/done/"
# A real cookie session without the sessions database table, plus the normal CSRF cookie.
STACK = {
    "PUBLIC_ORIGIN": ORIGIN,
    "ALLOWED_HOSTS": ["testserver"],
    "ROOT_URLCONF": "django_core_micha.notifications.tests.urls_push_actions",
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
    "MIDDLEWARE": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ],
}


@pytest.fixture
def setup_action():
    owner = get_user_model().objects.create_user(username="owner", password="password")
    other = get_user_model().objects.create_user(username="other", password="password")
    notification = Notification.objects.create(notification_type="done", category="system", content={}, dedup_key="endpoint")
    recipient = NotificationRecipient.objects.create(notification=notification, user=owner)
    delivery = NotificationDelivery.objects.create(recipient=recipient, channel="push")

    def make_token(cleartext, expires_at=None, consumed_at=None):
        return NotificationPushActionToken.objects.create(
            delivery=delivery,
            recipient=recipient,
            action="done",
            token_hash=sha256(cleartext.encode()).hexdigest(),
            expires_at=expires_at or timezone.now() + timedelta(days=1),
            consumed_at=consumed_at,
        )

    return owner, other, recipient, make_token


def authenticated_client(account):
    client = Client(enforce_csrf_checks=True)
    assert client.login(username=account.username, password="password")
    response = client.get(CSRF_URL, HTTP_ORIGIN=ORIGIN)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    token = response.json()["csrf_token"]
    assert len(token) == 64
    return client, token


def post_done(client, token, csrf, origin=ORIGIN):
    return client.post(DONE_URL, data=json.dumps({"token": token}), content_type="application/json", HTTP_ORIGIN=origin, HTTP_X_CSRFTOKEN=csrf)


@pytest.mark.django_db(transaction=True)
@override_settings(**STACK, CSRF_TRUSTED_ORIGINS=["http://trusted.example.test"])
def test_session_csrf_origin_and_recipient_boundaries(setup_action):
    owner, other, recipient, make_token = setup_action
    token = "a" * 44
    row = make_token(token)
    client, csrf = authenticated_client(owner)
    missing = client.post(DONE_URL, data=json.dumps({"token": token}), content_type="application/json", HTTP_ORIGIN=ORIGIN)
    invalid = post_done(client, token, "invalid")
    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert missing.json() == invalid.json() == {"detail": "Denied."}
    for origin in ("https://evil.example", "http://trusted.example.test"):
        assert client.get(CSRF_URL, HTTP_ORIGIN=origin).status_code == 403
        assert post_done(client, token, csrf, origin).status_code == 403
    other_client, other_csrf = authenticated_client(other)
    assert post_done(other_client, token, other_csrf).status_code == 403
    client.logout()
    assert client.get(CSRF_URL).status_code in (401, 403)
    assert post_done(client, token, csrf).status_code in (401, 403)
    assert Client(enforce_csrf_checks=True).get(CSRF_URL, HTTP_AUTHORIZATION="Basic b3duZXI6cGFzc3dvcmQ=").status_code in (401, 403)
    recipient.refresh_from_db()
    row.refresh_from_db()
    assert recipient.done_at is None
    assert row.consumed_at is None


@pytest.mark.django_db(transaction=True)
@override_settings(**STACK)
def test_expiry_replay_and_two_tokens_emit_once(setup_action, monkeypatch):
    owner, _, recipient, make_token = setup_action
    expired = "e" * 44
    first = "f" * 44
    second = "s" * 44
    expired_row = make_token(expired, expires_at=timezone.now() - timedelta(seconds=1))
    make_token(first)
    make_token(second)
    events = []
    monkeypatch.setattr("django_core_micha.notifications.views.push_to_users", lambda *args: events.append(args))
    client, csrf = authenticated_client(owner)
    assert post_done(client, expired, csrf).status_code == 403
    expired_row.refresh_from_db()
    assert expired_row.consumed_at is None
    assert post_done(client, first, csrf).status_code == 204
    assert post_done(client, first, csrf).status_code == 403
    assert post_done(client, second, csrf).status_code == 204
    recipient.refresh_from_db()
    assert recipient.done_at is not None
    assert len(events) == 1
    assert events[0][0] == [owner]
    assert events[0][1]["type"] == "notification.status"
    assert events[0][1]["notification_id"] == recipient.notification_id
    assert events[0][1]["status"] == {"seen": False, "dismissed": False, "done": True}
    assert first not in json.dumps(events[0][1], default=str)


@pytest.mark.django_db(transaction=True)
@override_settings(**STACK)
def test_rolled_back_status_keeps_token_usable(setup_action, monkeypatch):
    owner, _, recipient, make_token = setup_action
    cleartext = "r" * 44
    token = make_token(cleartext)
    events = []
    monkeypatch.setattr("django_core_micha.notifications.views.push_to_users", lambda *args: events.append(args))
    client, csrf = authenticated_client(owner)
    with transaction.atomic():
        assert post_done(client, cleartext, csrf).status_code == 204
        transaction.set_rollback(True)
    token.refresh_from_db()
    recipient.refresh_from_db()
    assert token.consumed_at is None
    assert recipient.done_at is None
    assert not events
    assert post_done(client, cleartext, csrf).status_code == 204
    assert len(events) == 1


@pytest.mark.django_db(transaction=True)
def test_rolled_back_issuance_never_sends(monkeypatch):
    original = _REGISTRY.copy()
    _REGISTRY.clear()
    try:
        register_notification_type(NotificationType(key="rollback", category="system", mode="event", resolution="user-done", active=True, critical=True, push_done_label_key="done"))
        owner = get_user_model().objects.create_user(username="rollback", password="password")
        PushSubscription.objects.create(user=owner, endpoint="https://fcm.googleapis.com/fcm/send/rollback", p256dh="key", auth="auth")
        sent = []
        resolvable_at_send = []

        def record_send(**kwargs):
            # An immediate click must find the committed token and delivery, so look them up now.
            digest = sha256(kwargs["extras"]["done_token"].encode()).hexdigest()
            resolvable_at_send.append(
                NotificationPushActionToken.objects.filter(token_hash=digest, delivery__isnull=False).exists()
            )
            sent.append(kwargs)

        monkeypatch.setattr(dispatch, "_send_push", record_send)
        with transaction.atomic():
            notify(type="rollback", recipients=owner, channels=["push"], content={"title_key": "Title", "body_key": "Body"})
            transaction.set_rollback(True)
        assert not sent
        assert not NotificationPushActionToken.objects.exists()
        # Positive control: the same call outside a rolled-back transaction does send.
        notify(type="rollback", recipients=owner, channels=["push"], content={"title_key": "Title", "body_key": "Body"})
        assert len(sent) == 1
        assert resolvable_at_send == [True]
        assert NotificationPushActionToken.objects.count() == 1
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(original)


@pytest.mark.django_db(transaction=True)
@override_settings(**STACK)
def test_token_for_another_action_is_rejected(setup_action):
    owner, _, recipient, make_token = setup_action
    cleartext = "w" * 44
    row = make_token(cleartext)
    row.action = "dismiss"
    row.save(update_fields=["action"])
    client, csrf = authenticated_client(owner)
    assert post_done(client, cleartext, csrf).status_code == 403
    recipient.refresh_from_db()
    row.refresh_from_db()
    assert recipient.done_at is None
    assert row.consumed_at is None


@pytest.mark.django_db(transaction=True)
@override_settings(**STACK)
def test_simultaneous_clicks_consume_a_token_once(setup_action, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connection

    owner, _, recipient, make_token = setup_action
    cleartext = "p" * 44
    make_token(cleartext)
    events = []
    monkeypatch.setattr("django_core_micha.notifications.views.push_to_users", lambda *args: events.append(args))
    barrier = Barrier(2)

    def click():
        try:
            client, csrf = authenticated_client(owner)
            barrier.wait(timeout=20)
            return post_done(client, cleartext, csrf).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(click), pool.submit(click)]
        statuses = sorted(future.result() for future in futures)
    assert statuses == [204, 403]
    recipient.refresh_from_db()
    assert recipient.done_at is not None
    assert len(events) == 1


@pytest.mark.django_db
def test_prune_tokens_without_pruning_notifications(setup_action):
    _, _, _, make_token = setup_action
    old = timezone.now() - timedelta(days=15)
    expired = make_token("e" * 44, expires_at=old)
    consumed = make_token("c" * 44, consumed_at=old)
    keep = make_token("k" * 44)
    call_command("prune_notifications", verbosity=0)
    assert not NotificationPushActionToken.objects.filter(pk__in=[expired.pk, consumed.pk]).exists()
    assert NotificationPushActionToken.objects.filter(pk=keep.pk).exists()
    assert Notification.objects.count() == 1
