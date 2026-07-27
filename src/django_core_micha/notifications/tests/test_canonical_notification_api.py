from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.notifications import views as notification_views
from django_core_micha.notifications.models import Notification, NotificationRecipient
from django_core_micha.notifications.serializers import CanonicalNotificationSerializer
from django_core_micha.notifications.views import CanonicalInboxView, CanonicalMarkView, CanonicalUnreadCountView


def make_user(username):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="password",
    )


def make_recipient(user, *, suffix, created_at=None, seen_at=None, dismissed_at=None, done_at=None, notifiable=None):
    notification = Notification.objects.create(
        notification_type="test_notice",
        category="system",
        urgency="normal",
        content={"title_key": "Notification.Title", "body_key": "Notification.Body", "params": {"key": suffix}},
        dedup_key=f"canonical-{suffix}",
        notifiable=notifiable,
    )
    if created_at is not None:
        Notification.objects.filter(pk=notification.pk).update(created_at=created_at)
        notification.refresh_from_db()
    return NotificationRecipient.objects.create(
        notification=notification,
        user=user,
        seen_at=seen_at,
        dismissed_at=dismissed_at,
        done_at=done_at,
    )


def get_feed(user, **query):
    request = APIRequestFactory().get("/notifications/feed/", query)
    force_authenticate(request, user=user)
    return CanonicalInboxView.as_view()(request)


def post_mark(user, data):
    request = APIRequestFactory().post("/notifications/feed/mark/", data, format="json")
    force_authenticate(request, user=user)
    return CanonicalMarkView.as_view()(request)


@pytest.mark.django_db
def test_canonical_serializer_flattens_recipient_without_notifiable_content_leak():
    user = make_user("serializer-owner")
    recipient = make_recipient(user, suffix="serializer", notifiable=user)

    serializer = CanonicalNotificationSerializer(recipient)

    assert set(serializer.data) == {
        "id",
        "notification_id",
        "notification_type",
        "category",
        "urgency",
        "content",
        "notifiable",
        "created_at",
        "seen_at",
        "dismissed_at",
        "done_at",
    }
    assert serializer.data["id"] == recipient.pk
    assert serializer.data["notification_id"] == recipient.notification_id
    assert serializer.data["notifiable"] == {
        "content_type": "auth.user",
        "object_id": str(user.pk),
    }
    assert "username" not in serializer.data["notifiable"]
    assert all(field.read_only for field in serializer.fields.values())
    input_serializer = CanonicalNotificationSerializer(data={})
    assert input_serializer.is_valid()
    with pytest.raises(NotImplementedError):
        input_serializer.save()


@pytest.mark.django_db
def test_canonical_feed_is_self_scoped_newest_first_and_paginated():
    owner = make_user("feed-owner")
    other_user = make_user("feed-other")
    now = timezone.now()
    oldest = make_recipient(owner, suffix="oldest", created_at=now - timedelta(days=2))
    newest = make_recipient(owner, suffix="newest", created_at=now)
    make_recipient(other_user, suffix="other", created_at=now + timedelta(days=1))
    for index in range(19):
        make_recipient(owner, suffix=f"page-{index}", created_at=now - timedelta(minutes=index + 1))

    response = get_feed(owner)

    assert response.status_code == 200
    assert response.data["count"] == 21
    assert len(response.data["results"]) == 20
    assert response.data["results"][0]["id"] == newest.pk
    assert oldest.pk not in [item["id"] for item in response.data["results"]]
    second_page = get_feed(owner, page=2)
    assert [item["id"] for item in second_page.data["results"]] == [oldest.pk]


@pytest.mark.django_db
def test_canonical_feed_status_filters():
    user = make_user("status-filters")
    now = timezone.now()
    unseen = make_recipient(user, suffix="unseen")
    active_seen = make_recipient(user, suffix="active-seen", seen_at=now)
    dismissed = make_recipient(user, suffix="dismissed", dismissed_at=now)
    done = make_recipient(user, suffix="done", done_at=now)

    unseen_ids = {item["id"] for item in get_feed(user, status="unseen").data["results"]}
    active_ids = {item["id"] for item in get_feed(user, status="active").data["results"]}
    done_ids = {item["id"] for item in get_feed(user, status="done").data["results"]}

    assert unseen_ids == {unseen.pk, dismissed.pk, done.pk}
    assert active_ids == {unseen.pk, active_seen.pk}
    assert done_ids == {done.pk}


@pytest.mark.django_db
def test_canonical_unread_count_excludes_dismissed_unseen_rows():
    user = make_user("unread-count")
    other_user = make_user("unread-other")
    now = timezone.now()
    make_recipient(user, suffix="unread")
    make_recipient(user, suffix="dismissed-unseen", dismissed_at=now)
    make_recipient(user, suffix="seen", seen_at=now)
    make_recipient(other_user, suffix="other-unread")
    request = APIRequestFactory().get("/notifications/feed/unread-count/")
    force_authenticate(request, user=user)

    response = CanonicalUnreadCountView.as_view()(request)

    assert response.status_code == 200
    assert response.data == {"count": 1}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("action", "field"),
    [("seen", "seen_at"), ("dismissed", "dismissed_at"), ("done", "done_at")],
)
def test_canonical_mark_stamps_the_requested_status(action, field, monkeypatch):
    user = make_user(f"mark-{action}")
    recipient = make_recipient(user, suffix=f"mark-{action}")
    monkeypatch.setattr(notification_views, "push_to_users", lambda users, payload: None)

    response = post_mark(user, {"action": action, "ids": [recipient.pk]})

    recipient.refresh_from_db()
    assert response.status_code == 200
    assert response.data == {"updated": 1}
    for timestamp_field in ("seen_at", "dismissed_at", "done_at"):
        if timestamp_field == field:
            assert getattr(recipient, timestamp_field) is not None
        else:
            assert getattr(recipient, timestamp_field) is None


@pytest.mark.django_db
def test_canonical_mark_refreshes_an_existing_timestamp(monkeypatch):
    user = make_user("mark-refresh")
    recipient = make_recipient(user, suffix="mark-refresh")
    timestamps = iter([timezone.now() - timedelta(minutes=1), timezone.now()])
    monkeypatch.setattr(notification_views.timezone, "now", lambda: next(timestamps))
    monkeypatch.setattr(notification_views, "push_to_users", lambda users, payload: None)

    assert post_mark(user, {"action": "seen", "ids": [recipient.pk]}).data == {"updated": 1}
    recipient.refresh_from_db()
    first_seen_at = recipient.seen_at
    assert post_mark(user, {"action": "seen", "ids": [recipient.pk]}).data == {"updated": 1}
    recipient.refresh_from_db()

    assert recipient.seen_at > first_seen_at


@pytest.mark.django_db
def test_canonical_mark_silently_excludes_another_users_recipient():
    owner = make_user("mark-owner")
    attacker = make_user("mark-attacker")
    recipient = make_recipient(owner, suffix="mark-idor")

    response = post_mark(attacker, {"action": "done", "ids": [recipient.pk]})

    recipient.refresh_from_db()
    assert response.status_code == 200
    assert response.data == {"updated": 0}
    assert recipient.done_at is None


@pytest.mark.django_db
def test_canonical_feed_never_includes_another_users_recipients():
    owner = make_user("feed-idor-owner")
    viewer = make_user("feed-idor-viewer")
    owner_recipient = make_recipient(owner, suffix="feed-idor-owner")
    viewer_recipient = make_recipient(viewer, suffix="feed-idor-viewer")

    response = get_feed(viewer)

    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [viewer_recipient.pk]
    assert owner_recipient.pk not in [item["id"] for item in response.data["results"]]


@pytest.mark.django_db
def test_canonical_mark_broadcasts_one_full_status_payload_per_notification(monkeypatch):
    user = make_user("broadcast")
    now = timezone.now()
    seen = make_recipient(user, suffix="broadcast-seen", seen_at=now)
    dismissed = make_recipient(user, suffix="broadcast-dismissed", dismissed_at=now)
    calls = []
    monkeypatch.setattr(notification_views, "push_to_users", lambda users, payload: calls.append((users, payload)))

    response = post_mark(user, {"action": "done", "ids": [dismissed.pk, seen.pk, seen.pk]})

    assert response.data == {"updated": 2}
    assert calls == [
        (
            [user],
            {
                "type": "notification.status",
                "notification_id": seen.notification_id,
                "status": {"seen": True, "dismissed": False, "done": True},
            },
        ),
        (
            [user],
            {
                "type": "notification.status",
                "notification_id": dismissed.notification_id,
                "status": {"seen": False, "dismissed": True, "done": True},
            },
        ),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("data", [{"ids": []}, {"action": "invalid", "ids": []}, {"action": "seen", "ids": "bad"}])
def test_canonical_mark_rejects_invalid_input(data):
    user = make_user(f"invalid-{str(data)}")

    response = post_mark(user, data)

    assert response.status_code == 400
