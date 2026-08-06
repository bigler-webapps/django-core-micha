from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from django_core_micha.notifications.models import Notification, NotificationRecipient
from django_core_micha.notifications.todo import registry
from django_core_micha.notifications.todo.models import TodoOverride
from django_core_micha.notifications.todo.registry import TodoSeed, TodoTypeConfig
from django_core_micha.notifications.todo.service import derive_active_todos, derive_todos_for_user, sync_todos_for_user
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


@pytest.mark.django_db
def test_warm_derivation_has_a_bounded_query_count_for_many_types():
    user, _ = make_user_and_widget()
    widgets = [Widget.objects.create(name=f"Scope {index}") for index in range(13)]
    for index, widget in enumerate(widgets):
        type_key = f"todo-{index}"
        register_notification_type(NotificationType(
            key=type_key, category="todo", mode="provider", resolution="state-resolved",
        ))
        registry.register_todo_provider(
            type_key,
            lambda user, now, *, type_key=type_key, widget=widget: [
                TodoSeed(type_key, user, {"title": type_key}, widget)
            ],
            config=TodoTypeConfig(type_key=type_key),
        )

    with CaptureQueriesContext(connection) as cold_queries:
        derive_todos_for_user(user)
    with CaptureQueriesContext(connection) as warm_queries:
        recipients = derive_todos_for_user(user)

    assert len(recipients) == 13
    # Set reads/inserts replace the prior 39 per-seed queries in both cases. The
    # count stays bounded (not scaling with the number of providers) as providers add
    # emitted seeds. Ceilings (not exact equality): ContentType.get_for_models' internal
    # cache means the exact count varies by +/-1 depending on whether an earlier test in
    # the same process already warmed the cache for Widget.
    assert len(cold_queries) <= 8
    assert len(warm_queries) <= 3


@pytest.mark.django_db
def test_multiple_types_apply_overrides_and_keep_materialized_output():
    user, _ = make_user_and_widget()
    disabled_scope = Widget.objects.create(name="Disabled")
    lead_scope = Widget.objects.create(name="Lead")
    plain_scope = Widget.objects.create(name="Plain")
    now = timezone.now()
    definitions = [
        ("disabled", disabled_scope, TodoTypeConfig(type_key="disabled", always_visible=True)),
        ("lead", lead_scope, TodoTypeConfig(type_key="lead", due="start", remind_before="P1D", lead_adjustable=True)),
        ("plain", plain_scope, TodoTypeConfig(type_key="plain", always_visible=True)),
    ]
    for type_key, scope, config in definitions:
        register_notification_type(NotificationType(
            key=type_key, category="todo", mode="provider", resolution="state-resolved",
        ))
        registry.register_todo_provider(
            type_key,
            lambda user, now, *, type_key=type_key, scope=scope: [
                TodoSeed(type_key, user, {"title": type_key}, scope, scope=scope,
                         due_base_resolver=lambda base: now + timedelta(days=2))
            ],
            config=config,
        )
    content_type = ContentType.objects.get_for_model(disabled_scope)
    TodoOverride.objects.create(content_type=content_type, object_id=str(disabled_scope.pk), type_key="disabled", enabled=False)
    TodoOverride.objects.create(
        content_type=ContentType.objects.get_for_model(lead_scope), object_id=str(lead_scope.pk),
        type_key="lead", lead_days_override=3,
    )

    recipients = derive_todos_for_user(user, now)

    assert {recipient.notification.notification_type for recipient in recipients} == {"lead", "plain"}
    content_by_type = {recipient.notification.notification_type: recipient.notification.content for recipient in recipients}
    assert content_by_type["lead"] == {"title": "lead", "due": (now + timedelta(days=2)).date().isoformat(), "severity": "low"}
    assert content_by_type["plain"] == {"title": "plain", "due": None, "severity": "low"}


@pytest.mark.django_db
@pytest.mark.parametrize("status_field", ["dismissed_at", "done_at"])
def test_status_overlays_survive_consecutive_derivations(status_field):
    user, widget = make_user_and_widget()
    register_provider(lambda user, now: [TodoSeed("demo_todo", user, {"title": "Do"}, widget)], TodoTypeConfig(type_key="demo_todo"))
    recipient = derive_todos_for_user(user)[0]
    NotificationRecipient.objects.filter(pk=recipient.pk).update(**{status_field: timezone.now()})

    derive_todos_for_user(user)
    derive_todos_for_user(user)

    persisted = NotificationRecipient.objects.get(pk=recipient.pk)
    assert getattr(persisted, status_field) is not None
    assert derive_active_todos(user) == []


@pytest.mark.django_db
def test_stale_existing_content_is_resynced_in_a_bulk_update():
    user, widget = make_user_and_widget()
    register_provider(lambda user, now: [TodoSeed("demo_todo", user, {"title": "Fresh"}, widget)], TodoTypeConfig(type_key="demo_todo"))
    notification = derive_todos_for_user(user)[0].notification
    Notification.objects.filter(pk=notification.pk).update(content={"title": "Stale"})

    refreshed = derive_todos_for_user(user)[0].notification

    assert refreshed.content == {"title": "Fresh", "due": None, "severity": "low"}
    assert Notification.objects.get(pk=notification.pk).content == refreshed.content


@pytest.mark.django_db(transaction=True)
def test_bulk_upserts_converge_when_rows_appear_in_the_race_window(monkeypatch):
    user, widget = make_user_and_widget()
    register_provider(lambda user, now: [TodoSeed("demo_todo", user, {"title": "Race"}, widget)], TodoTypeConfig(type_key="demo_todo"))
    notification_bulk_create = Notification.objects.bulk_create
    recipient_bulk_create = NotificationRecipient.objects.bulk_create

    def notification_race(objects, **kwargs):
        competing = objects[0]
        Notification.objects.create(
            dedup_key=competing.dedup_key, notification_type=competing.notification_type,
            category=competing.category, urgency=competing.urgency, content=competing.content,
            content_type=competing.content_type, object_id=competing.object_id,
        )
        return notification_bulk_create(objects, **kwargs)

    def recipient_race(objects, **kwargs):
        competing = objects[0]
        NotificationRecipient.objects.create(notification_id=competing.notification_id, user_id=competing.user_id)
        return recipient_bulk_create(objects, **kwargs)

    monkeypatch.setattr(Notification.objects, "bulk_create", notification_race)
    monkeypatch.setattr(NotificationRecipient.objects, "bulk_create", recipient_race)

    recipients = derive_todos_for_user(user)

    assert len(recipients) == 1
    assert Notification.objects.count() == 1
    assert NotificationRecipient.objects.count() == 1
