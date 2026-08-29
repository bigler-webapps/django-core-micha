import json

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from django_core_micha.notifications.models import Notification, NotificationRecipient

from .test_canonical_notification_api import get_feed, get_unread_count, make_user


APP_LABEL = "django_core_micha_notifications"
MIGRATION_0009 = "0009_notification_resolved_at"
MIGRATION_0010 = "0010_notification_feed_query_indexes"
RECIPIENT_FEED_INDEX_FIELDS = ["user", "seen_at", "dismissed_at"]


def _matching_indexes(table_name, columns):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)
    return [
        name
        for name, constraint in constraints.items()
        if constraint["index"] and constraint["columns"] == columns
    ]


def _seed_recipient_history(user, count=400):
    now = timezone.now()
    notifications = [
        Notification(
            notification_type="test_notice",
            category="system",
            urgency="normal",
            content={"title_key": "Notification.Title", "body_key": "Notification.Body", "params": {}},
            dedup_key=f"feed-index-{index}",
        )
        for index in range(count)
    ]
    Notification.objects.bulk_create(notifications, batch_size=200)
    NotificationRecipient.objects.bulk_create(
        [
            NotificationRecipient(
                notification=notification,
                user=user,
                seen_at=None if index < 20 else now,
                dismissed_at=None if index < 20 else now,
            )
            for index, notification in enumerate(notifications)
        ],
        batch_size=200,
    )


def _explain_analyze(sql):
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
        plan = cursor.fetchone()[0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    return plan[0]["Plan"]


def _plan_nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _plan_nodes(child)


@pytest.mark.django_db(transaction=True)
def test_0010_migration_adds_and_removes_feed_indexes_on_populated_tables():
    executor = MigrationExecutor(connection)
    executor.migrate([(APP_LABEL, MIGRATION_0009)])
    old_apps = executor.loader.project_state([(APP_LABEL, MIGRATION_0009)]).apps
    old_notification = old_apps.get_model(APP_LABEL, "Notification")
    old_recipient = old_apps.get_model(APP_LABEL, "NotificationRecipient")
    user = get_user_model().objects.create_user(
        username="feed-index-migration",
        email="feed-index-migration@example.test",
        password="password",
    )
    notifications = [
        old_notification(
            notification_type="test_notice",
            category="system",
            urgency="normal",
            content={"title_key": "Notification.Title", "body_key": "Notification.Body", "params": {}},
            dedup_key=f"migration-feed-index-{index}",
        )
        for index in range(400)
    ]

    try:
        old_notification.objects.bulk_create(notifications, batch_size=200)
        old_recipient.objects.bulk_create(
            [old_recipient(notification_id=notification.pk, user_id=user.pk) for notification in notifications],
            batch_size=200,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([(APP_LABEL, MIGRATION_0010)])

        assert _matching_indexes(NotificationRecipient._meta.db_table, ["user_id", "seen_at", "dismissed_at"])
        assert _matching_indexes(Notification._meta.db_table, ["created_at"])

        executor = MigrationExecutor(connection)
        executor.migrate([(APP_LABEL, MIGRATION_0009)])

        assert not _matching_indexes(NotificationRecipient._meta.db_table, ["user_id", "seen_at", "dismissed_at"])
        assert not _matching_indexes(Notification._meta.db_table, ["created_at"])
    finally:
        call_command("migrate", run_syncdb=True, verbosity=0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="requires PostgreSQL EXPLAIN ANALYZE")
def test_postgresql_uses_feed_status_index_for_canonical_feed_and_unread_count():
    user = make_user("feed-index-plan")
    _seed_recipient_history(user, count=4_000)
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {connection.ops.quote_name(NotificationRecipient._meta.db_table)}")
        cursor.execute(f"ANALYZE {connection.ops.quote_name(Notification._meta.db_table)}")

    with CaptureQueriesContext(connection) as feed_queries:
        feed_response = get_feed(user, status="unseen")
    with CaptureQueriesContext(connection) as unread_queries:
        unread_response = get_unread_count(user)

    assert feed_response.status_code == 200
    assert unread_response.data == {"count": 20}

    feed_sql = next(query["sql"] for query in feed_queries if "ORDER BY" in query["sql"])
    unread_sql = next(
        query["sql"]
        for query in unread_queries
        if "COUNT" in query["sql"] and "seen_at" in query["sql"] and "dismissed_at" in query["sql"]
    )
    feed_index_name = next(
        index.name
        for index in NotificationRecipient._meta.indexes
        if index.fields == RECIPIENT_FEED_INDEX_FIELDS
    )

    for sql in (feed_sql, unread_sql):
        assert any(
            node.get("Index Name") == feed_index_name
            for node in _plan_nodes(_explain_analyze(sql))
        ), sql


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="requires PostgreSQL EXPLAIN ANALYZE")
def test_postgresql_uses_notification_created_at_index_for_the_feed_sort_key():
    """The composite recipient index (checked above) does not cover ordering by
    ``notification__created_at`` — that is what ``Notification.created_at``'s own
    index is for. Verify it separately, against a large, unfiltered table (the
    scenario the WO's root cause describes: the sort growing expensive as the
    table grows), rather than asserting it inside the per-user joined feed plan,
    where the recipient filter's own index is what the planner is expected to pick.
    """
    notifications = [
        Notification(
            notification_type="test_notice",
            category="system",
            urgency="normal",
            content={"title_key": "Notification.Title", "body_key": "Notification.Body", "params": {}},
            dedup_key=f"created-at-order-{index}",
        )
        for index in range(20_000)
    ]
    Notification.objects.bulk_create(notifications, batch_size=500)
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {connection.ops.quote_name(Notification._meta.db_table)}")

    with CaptureQueriesContext(connection) as queries:
        list(Notification.objects.order_by("-created_at")[:20])
    order_sql = next(query["sql"] for query in queries if "ORDER BY" in query["sql"])

    created_at_index_name = next(
        name for name in _matching_indexes(Notification._meta.db_table, ["created_at"])
    )

    assert any(
        node.get("Index Name") == created_at_index_name
        for node in _plan_nodes(_explain_analyze(order_sql))
    ), order_sql
