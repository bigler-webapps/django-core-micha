from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.notifications import views as notification_views
from django_core_micha.notifications.models import Notification, NotificationRecipient
from django_core_micha.notifications.serializers import CanonicalNotificationSerializer
from django_core_micha.notifications.todo import registry
from django_core_micha.notifications.todo.registry import TodoSeed, TodoTypeConfig
from django_core_micha.notifications.types import NotificationType, _REGISTRY, register_notification_type
from django_core_micha.notifications.views import CanonicalInboxView, CanonicalMarkView, CanonicalUnreadCountView
from tests.testapp.models import Widget


@pytest.fixture(autouse=True)
def clear_todo_registries():
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()
    yield
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()


def make_user(username):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="password",
    )


def make_recipient(
    user, *, suffix, notification_type="test_notice", created_at=None, seen_at=None, dismissed_at=None,
    done_at=None, notifiable=None,
):
    notification = Notification.objects.create(
        notification_type=notification_type,
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


def get_unread_count(user):
    request = APIRequestFactory().get("/notifications/feed/unread-count/")
    force_authenticate(request, user=user)
    return CanonicalUnreadCountView.as_view()(request)


def register_toggleable_todo_provider(emissions, widgets):
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
        default_channels=["todo"], eligible_channels=["todo"],
    ))

    def provider(user, now):
        return [TodoSeed("demo_todo", user, {"title": title}, widgets[title]) for title in emissions]

    registry.register_todo_provider(
        "demo_todo",
        provider,
        config=TodoTypeConfig(type_key="demo_todo", always_visible=True),
    )


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
    assert get_unread_count(owner).data == {"count": 21}


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
def test_unread_count_matches_the_unseen_live_feed_without_materializing_rows():
    user = make_user("count-list-agreement")
    emissions = {"First", "Second"}
    widgets = {title: Widget.objects.create(name=title) for title in emissions}
    register_toggleable_todo_provider(emissions, widgets)

    # The badge follows the same live projection but does not create the overlay
    # rows merely to count unseen actionable todos.
    assert get_unread_count(user).data == {"count": 2}
    assert Notification.objects.filter(category="todo").count() == 0

    unseen_feed = get_feed(user, status="unseen")
    assert len(unseen_feed.data["results"]) == 2
    first_recipient = unseen_feed.data["results"][0]
    NotificationRecipient.objects.filter(pk=first_recipient["id"]).update(seen_at=timezone.now())

    assert get_unread_count(user).data == {"count": 1}
    assert len(get_feed(user, status="unseen").data["results"]) == 1


@pytest.mark.django_db
def test_feed_visible_policy_hides_only_explicitly_hidden_registered_types():
    user = make_user("feed-visibility")
    register_notification_type(NotificationType(
        key="hidden-delivery-only", category="system", mode="event", resolution="user-done",
        default_channels=["email"], eligible_channels=["email"], feed_visible=False,
    ))
    visible_type = NotificationType(
        key="visible-default", category="system", mode="event", resolution="user-done",
        default_channels=["chip"], eligible_channels=["chip"],
    )
    register_notification_type(visible_type)
    hidden = make_recipient(user, suffix="hidden", notification_type="hidden-delivery-only")
    visible = make_recipient(user, suffix="visible", notification_type="visible-default")
    historical = make_recipient(user, suffix="historical", notification_type="removed-registration")

    response = get_feed(user)

    assert visible_type.feed_visible is True
    assert {item["id"] for item in response.data["results"]} == {visible.pk, historical.pk}
    assert hidden.pk not in {item["id"] for item in response.data["results"]}
    assert get_unread_count(user).data == {"count": 2}


@pytest.mark.django_db
def test_canonical_feed_and_unread_count_self_heal_when_provider_stops_emitting():
    user = make_user("todo-self-heal")
    emissions = {"Pay invoice"}
    register_toggleable_todo_provider(emissions, {"Pay invoice": Widget.objects.create(name="Invoice")})

    first_feed = get_feed(user)
    assert first_feed.status_code == 200
    assert [item["content"]["title"] for item in first_feed.data["results"]] == ["Pay invoice"]
    assert get_unread_count(user).data == {"count": 1}

    emissions.clear()

    assert get_feed(user).data["results"] == []
    assert get_unread_count(user).data == {"count": 0}
    assert Notification.objects.filter(category="todo").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status_field", ["dismissed_at", "done_at"])
def test_emitted_dismissed_or_done_todo_stays_hidden_on_active_reads(status_field):
    user = make_user(f"todo-{status_field}")
    emissions = {"Do work"}
    register_toggleable_todo_provider(emissions, {"Do work": Widget.objects.create(name="Work")})

    recipient_id = get_feed(user).data["results"][0]["id"]
    NotificationRecipient.objects.filter(pk=recipient_id).update(**{status_field: timezone.now()})

    # Only status=active hides it (matching event-authored semantics) -- the
    # unfiltered/done-filtered parity is covered by the test below.
    assert get_feed(user, status="active").data["results"] == []
    assert get_unread_count(user).data == {"count": 0}
    recipient = NotificationRecipient.objects.get(pk=recipient_id)
    assert getattr(recipient, status_field) is not None


@pytest.mark.django_db
@pytest.mark.parametrize("status_field", ["dismissed_at", "done_at"])
def test_dismissed_or_done_todo_still_appears_unfiltered_and_under_its_own_status_filter(status_field):
    # Parity with event-authored notifications: a dismissed/done row still shows up in
    # the default (unfiltered) view, and a done row still shows up under status=done.
    user = make_user(f"todo-parity-{status_field}")
    emissions = {"Do work"}
    register_toggleable_todo_provider(emissions, {"Do work": Widget.objects.create(name="Work")})

    recipient_id = get_feed(user).data["results"][0]["id"]
    NotificationRecipient.objects.filter(pk=recipient_id).update(**{status_field: timezone.now()})

    assert [item["id"] for item in get_feed(user).data["results"]] == [recipient_id]
    if status_field == "done_at":
        assert [item["id"] for item in get_feed(user, status="done").data["results"]] == [recipient_id]


@pytest.mark.django_db
def test_feed_stays_db_paginated_when_no_todo_provider_is_registered(django_assert_num_queries):
    # Regression: merging live-derived todos into the feed must not force materializing
    # the user's entire event-authored notification history on every request when the
    # todo channel isn't even in use (the only real consumer of this endpoint today).
    user = make_user("no-todo-consumer")
    for index in range(30):
        make_recipient(user, suffix=f"scale-{index}")

    with django_assert_num_queries(2):
        # 1 COUNT for pagination, 1 SELECT for the page slice -- flat regardless of how
        # many notifications exist, proving SQL LIMIT/OFFSET is used, not a full fetch.
        response = get_feed(user)
    assert response.data["count"] == 30
    assert len(response.data["results"]) == 20


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
                "envelope": "notification",
            },
        ),
        (
            [user],
            {
                "type": "notification.status",
                "notification_id": dismissed.notification_id,
                "status": {"seen": False, "dismissed": True, "done": True},
                "envelope": "notification",
            },
        ),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("data", [{"ids": []}, {"action": "invalid", "ids": []}, {"action": "seen", "ids": "bad"}])
def test_canonical_mark_rejects_invalid_input(data):
    user = make_user(f"invalid-{str(data)}")

    response = post_mark(user, data)

    assert response.status_code == 400
