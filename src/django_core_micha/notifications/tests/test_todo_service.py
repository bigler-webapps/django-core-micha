from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from django_core_micha.notifications.models import Notification, NotificationRecipient
from django_core_micha.notifications.todo import registry
from django_core_micha.notifications.todo.models import TodoOverride
from django_core_micha.notifications.todo.registry import TodoSeed, TodoTypeConfig
from django_core_micha.notifications.todo.service import sync_todos_for_user
from django_core_micha.notifications.types import NotificationType, _REGISTRY, register_notification_type
from tests.testapp.models import Widget


@pytest.fixture(autouse=True)
def clear_registries():
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()
    yield
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()


def register_provider(provider, config):
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
        default_channels=["todo"], eligible_channels=["todo"],
    ))
    registry.register_todo_provider("demo_todo", provider, config=config)


def make_user_and_widget():
    user = get_user_model().objects.create_user(username="todo-user", email="todo@example.test", password="password")
    return user, Widget.objects.create(name="Todo scope")


@pytest.mark.django_db
def test_sync_materializes_once_and_keeps_persisted_content_fresh():
    user, widget = make_user_and_widget()
    now = timezone.now()
    register_provider(
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Pay", "_private": "x"}, widget, due_base_resolver=lambda base: now + timedelta(days=1))],
        TodoTypeConfig(type_key="demo_todo", due="start", remind_before="P2D", severity="medium"),
    )
    first = sync_todos_for_user(user, now)
    second = sync_todos_for_user(user, now)
    assert len(first) == len(second) == 1
    assert Notification.objects.count() == NotificationRecipient.objects.count() == 1
    # Persisted content is the full materialized view (not just the raw seed payload) so
    # every other canonical consumer (feed/, dispatchers) sees current due/severity too.
    assert first[0].content == Notification.objects.get(pk=first[0].pk).content
    assert first[0].content["title"] == "Pay"
    assert "_private" not in first[0].content
    assert first[0].content["severity"] == "medium"
    assert first[0].content["due"]


@pytest.mark.django_db
def test_disabled_override_suppresses_scope_and_lead_override_changes_visibility():
    user, widget = make_user_and_widget()
    now = timezone.now()
    seed = TodoSeed("demo_todo", user, {"title": "Pay"}, widget, scope=widget, due_base_resolver=lambda base: now + timedelta(days=2))
    config = TodoTypeConfig(type_key="demo_todo", due="start", remind_before="P1D", lead_adjustable=True)
    register_provider(lambda user, now: [seed], config)
    assert sync_todos_for_user(user, now) == []
    TodoOverride.objects.create(content_type=ContentType.objects.get_for_model(widget), object_id=str(widget.pk), type_key="demo_todo", lead_days_override=3)
    assert len(sync_todos_for_user(user, now)) == 1
    TodoOverride.objects.filter(type_key="demo_todo").update(enabled=False)
    assert sync_todos_for_user(user, now) == []


@pytest.mark.django_db
@pytest.mark.parametrize("status_field", ["dismissed_at", "done_at"])
def test_actioned_recipient_never_resurfaces(status_field):
    user, widget = make_user_and_widget()
    now = timezone.now()
    register_provider(lambda user, now: [TodoSeed("demo_todo", user, {"title": "Do"}, widget)], TodoTypeConfig(type_key="demo_todo"))
    notification = sync_todos_for_user(user, now)[0]
    NotificationRecipient.objects.filter(notification=notification, user=user).update(**{status_field: now})
    assert sync_todos_for_user(user, now) == []


@pytest.mark.django_db
def test_persisted_content_severity_refreshes_across_calls_as_due_date_passes():
    user, widget = make_user_and_widget()
    due = timezone.now()
    register_provider(
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Pay"}, widget, due_base_resolver=lambda base: due)],
        TodoTypeConfig(type_key="demo_todo", due="start", persist_until_done=True, severity="medium"),
    )
    fresh = sync_todos_for_user(user, due)[0]
    assert fresh.content["severity"] == "medium"
    stale = sync_todos_for_user(user, due + timedelta(days=1))[0]
    assert stale.pk == fresh.pk
    assert stale.content["severity"] == "overdue"
    assert Notification.objects.get(pk=fresh.pk).content["severity"] == "overdue"


@pytest.mark.django_db
def test_no_longer_visible_seed_is_omitted_without_deleting_existing_row():
    user, widget = make_user_and_widget()
    due = timezone.now()
    register_provider(lambda user, now: [TodoSeed("demo_todo", user, {"title": "Expired"}, widget, due_base_resolver=lambda base: due)], TodoTypeConfig(type_key="demo_todo", due="start"))
    assert len(sync_todos_for_user(user, due)) == 1
    assert sync_todos_for_user(user, due + timedelta(days=4)) == []
    assert Notification.objects.count() == 1
