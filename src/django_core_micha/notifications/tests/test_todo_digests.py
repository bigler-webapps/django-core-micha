from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from django_core_micha.notifications.models import Notification, NotificationDelivery, NotificationPreference, NotificationRecipient
from django_core_micha.notifications.todo import digests, registry
from django_core_micha.notifications.todo.registry import TodoSeed, TodoTypeConfig
from django_core_micha.notifications.types import NotificationType, _REGISTRY, register_notification_type
from tests.testapp.models import Widget


@pytest.fixture(autouse=True)
def clear_registries():
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()
    yield
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()


@pytest.mark.django_db
def test_digest_records_thresholds_once_and_sends_again_only_for_t2(monkeypatch):
    user = get_user_model().objects.create_user(username="digest-user", email="digest@example.test", password="password")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    widget = Widget.objects.create(name="Digest scope")
    first_now = timezone.now()
    due = first_now + timedelta(days=3)
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
        default_channels=["todo"], eligible_channels=["todo"],
    ))
    registry.register_todo_provider(
        "demo_todo",
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Digest task"}, widget, due_base_resolver=lambda base: due)],
        config=TodoTypeConfig(type_key="demo_todo", due="start", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )
    sent = []
    monkeypatch.setattr(digests, "_send_email", lambda **kwargs: sent.append(kwargs))

    first = digests.send_todo_digests(first_now)
    second = digests.send_todo_digests(first_now)
    third = digests.send_todo_digests(due - timedelta(hours=12))

    assert first.digests_sent == first.threshold_records_created == 1
    assert second.digests_sent == second.threshold_records_created == 0
    assert third.digests_sent == third.threshold_records_created == 1
    assert len(sent) == 2
    assert set(NotificationDelivery.objects.values_list("digest_threshold", flat=True)) == {"t1", "t2"}
    assert set(NotificationDelivery.objects.values_list("status", flat=True)) == {"sent"}


@pytest.mark.django_db
def test_send_failure_marks_delivery_failed_not_falsely_sent(monkeypatch):
    user = get_user_model().objects.create_user(username="flaky-mail", email="flaky@example.test", password="password")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    now = timezone.now()
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
        default_channels=["todo"], eligible_channels=["todo"],
    ))
    registry.register_todo_provider(
        "demo_todo",
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Digest task"}, Widget.objects.create(name="scope"), due_base_resolver=lambda base: now)],
        config=TodoTypeConfig(type_key="demo_todo", due="start", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )

    def _boom(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(digests, "_send_email", _boom)

    result = digests.send_todo_digests(now)
    assert result.digests_sent == 0
    assert result.threshold_records_created == 2
    assert set(NotificationDelivery.objects.values_list("status", flat=True)) == {"failed"}


@pytest.mark.django_db
def test_not_opted_in_user_records_nothing_and_later_opt_in_catches_up(monkeypatch):
    # Regression for a real reviewed bug: a threshold must never be recorded as
    # "sent" for a recipient who was never actually emailed, or a later opt-in
    # would silently never receive that reminder.
    user = get_user_model().objects.create_user(username="not-opted-in", email="lurker@example.test", password="password")
    now = timezone.now()
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
        default_channels=["todo"], eligible_channels=["todo"],
    ))
    registry.register_todo_provider(
        "demo_todo",
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Digest task"}, Widget.objects.create(name="scope"), due_base_resolver=lambda base: now)],
        config=TodoTypeConfig(type_key="demo_todo", due="start", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )
    sent = []
    monkeypatch.setattr(digests, "_send_email", lambda **kwargs: sent.append(kwargs))

    result = digests.send_todo_digests(now)
    assert result.digests_sent == result.threshold_records_created == 0
    assert not sent
    assert NotificationDelivery.objects.count() == 0

    NotificationPreference.objects.create(user=user, email_opt_in=True)
    caught_up = digests.send_todo_digests(now)
    # The seed is due right now, so both t1 (became visible) and t2 (within the
    # pre-due lead window) cross in the same run -- one email, two thresholds.
    assert caught_up.digests_sent == 1
    assert caught_up.threshold_records_created == 2
    assert len(sent) == 1


@pytest.mark.django_db
def test_digest_reconciles_only_non_emitted_todo_overlays_and_preserves_emitted_statuses():
    user = get_user_model().objects.create_user(username="reconcile-user", email="reconcile@example.test", password="password")
    now = timezone.now()
    widgets = {
        "stale": Widget.objects.create(name="Stale"),
        "dismissed": Widget.objects.create(name="Dismissed"),
        "done": Widget.objects.create(name="Done"),
    }
    emissions = {"stale", "dismissed", "done"}
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
        candidate_users_fn=lambda now: [user],
    )
    digests.send_todo_digests(now)
    recipients = {
        recipient.notification.content["title"]: recipient
        for recipient in NotificationRecipient.objects.filter(user=user, notification__category="todo").select_related("notification")
    }
    NotificationRecipient.objects.filter(pk=recipients["dismissed"].pk).update(dismissed_at=now)
    NotificationRecipient.objects.filter(pk=recipients["done"].pk).update(done_at=now)
    dismissed_id = recipients["dismissed"].notification_id
    done_id = recipients["done"].notification_id
    stale_id = recipients["stale"].notification_id

    emissions.remove("stale")
    digests.send_todo_digests(now)

    assert not Notification.objects.filter(pk=stale_id).exists()
    assert Notification.objects.filter(pk=dismissed_id, category="todo").exists()
    assert Notification.objects.filter(pk=done_id, category="todo").exists()
    dismissed = NotificationRecipient.objects.get(notification_id=dismissed_id, user=user)
    done = NotificationRecipient.objects.get(notification_id=done_id, user=user)
    assert dismissed.dismissed_at is not None
    assert done.done_at is not None


def test_management_command_reports_digest_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        "django_core_micha.notifications.management.commands.send_todo_digests.send_todo_digests",
        lambda: digests.DigestRunSummary(2, 1, 3),
    )
    call_command("send_todo_digests")
    assert "Scanned 2 users; sent 1 digests; recorded 3 thresholds." in capsys.readouterr().out
