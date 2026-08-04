import datetime

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.utils import timezone

from django_core_micha.activity.models import ActivityBucket
from django_core_micha.activity.policy import register_activity_policy, unregister_activity_policy
from django_core_micha.activity.services import (
    ActivityPermissionDenied,
    query_activity,
    record_ping,
)
from tests.testapp.models import Widget

CONTENT_TYPE_LABEL = "testapp.widget"
APP_KEY = "test-app"


class AllowPolicy:
    def can_read_activity(self, **kwargs):
        return True


class DenyPolicy:
    def can_read_activity(self, **kwargs):
        return False


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="activity-user")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="activity-user-2")


@pytest.fixture
def widget(db):
    return Widget.objects.create(name="Scope A")


@pytest.fixture
def other_widget(db):
    return Widget.objects.create(name="Scope B")


@pytest.fixture
def content_type(db):
    return ContentType.objects.get_by_natural_key("testapp", "widget")


@pytest.fixture
def allow_policy():
    register_activity_policy(APP_KEY, AllowPolicy())
    yield
    unregister_activity_policy(APP_KEY)


def _make_bucket(*, content_type, object_id, user, bucket_start, active_seconds, app_key=APP_KEY):
    return ActivityBucket.objects.create(
        app_key=app_key,
        content_type=content_type,
        object_id=str(object_id),
        user=user,
        bucket_start=bucket_start,
        active_seconds=active_seconds,
        last_ping_at=bucket_start,
    )


# --- Required test 1: same-bucket pings accumulate, not duplicate ---


@pytest.mark.django_db
def test_two_pings_in_same_bucket_accumulate_into_one_row(user, widget):
    record_ping(actor=user, app_key=APP_KEY, content_type_label=CONTENT_TYPE_LABEL, object_id=widget.pk)
    record_ping(actor=user, app_key=APP_KEY, content_type_label=CONTENT_TYPE_LABEL, object_id=widget.pk)

    rows = ActivityBucket.objects.filter(user=user)
    assert rows.count() == 1
    assert rows.get().active_seconds >= 0


# --- Required test 2: pings against different scope objects stay separate ---


@pytest.mark.django_db
def test_pings_against_different_scope_objects_stay_separate(user, widget, other_widget):
    record_ping(actor=user, app_key=APP_KEY, content_type_label=CONTENT_TYPE_LABEL, object_id=widget.pk)
    record_ping(actor=user, app_key=APP_KEY, content_type_label=CONTENT_TYPE_LABEL, object_id=other_widget.pk)

    assert ActivityBucket.objects.filter(user=user).count() == 2
    assert set(ActivityBucket.objects.filter(user=user).values_list("object_id", flat=True)) == {
        str(widget.pk),
        str(other_widget.pk),
    }


# --- Required test 3: rollup to requested granularity, row count matches coarse not stored ---


@pytest.mark.django_db
def test_query_rolls_up_to_requested_granularity_and_row_count_matches_coarse_not_stored(
    user, other_user, widget, content_type, allow_policy
):
    anchor = datetime.datetime(2026, 1, 15, 20, 0, tzinfo=datetime.timezone.utc)
    # Five distinct stored hourly buckets, all within the same calendar day.
    hours = [anchor - datetime.timedelta(hours=offset) for offset in (0, 3, 6, 9, 12)]
    for hour in hours:
        _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=hour, active_seconds=600)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=other_user, bucket_start=hours[0], active_seconds=1200)

    rows, granularity = query_activity(
        actor=user,
        app_key=APP_KEY,
        content_type_label=CONTENT_TYPE_LABEL,
        object_id=widget.pk,
        range_key="1m",
        anchor=anchor,
    )

    assert granularity == "day"
    # 6 stored rows (5 hourly for `user` + 1 for `other_user`), all on the same
    # calendar day -> exactly ONE returned row at day granularity, not six. A
    # rollup-less implementation (grouping by raw bucket_start) would return 5.
    assert len(rows) == 1
    row = rows[0]
    assert row["distinct_users"] == 2
    assert row["presence_hours"] == round((600 * 5 + 1200) / 3600, 2)


@pytest.mark.django_db
def test_query_4hour_granularity_groups_hourly_buckets_into_4hour_windows(user, widget, content_type, allow_policy):
    anchor = datetime.datetime(2026, 1, 15, 23, 0, tzinfo=datetime.timezone.utc)
    day_start = anchor.replace(hour=0)
    # Two rows in the [8,12) 4-hour window, one row in [12,16).
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=day_start + datetime.timedelta(hours=8), active_seconds=600)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=day_start + datetime.timedelta(hours=10), active_seconds=600)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=day_start + datetime.timedelta(hours=13), active_seconds=1200)

    rows, granularity = query_activity(
        actor=user,
        app_key=APP_KEY,
        content_type_label=CONTENT_TYPE_LABEL,
        object_id=widget.pk,
        range_key="1w",
        anchor=anchor,
    )

    assert granularity == "4hour"
    by_bucket = {row["bucket_start"]: row for row in rows}
    window_8 = by_bucket[day_start + datetime.timedelta(hours=8)]
    window_12 = by_bucket[day_start + datetime.timedelta(hours=12)]
    assert window_8["presence_hours"] == round(1200 / 3600, 2)
    assert window_12["presence_hours"] == round(1200 / 3600, 2)


# --- Required test 4: distinct users / presence-time aggregation correctness ---


@pytest.mark.django_db
def test_distinct_users_and_presence_aggregate_correctly(user, other_user, widget, content_type, allow_policy):
    anchor = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
    previous_hour = anchor - datetime.timedelta(hours=1)
    # Same bucket, two users sharing it.
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=anchor, active_seconds=1800)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=other_user, bucket_start=anchor, active_seconds=900)
    # One user, a second bucket.
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=previous_hour, active_seconds=3600)

    rows, granularity = query_activity(
        actor=user,
        app_key=APP_KEY,
        content_type_label=CONTENT_TYPE_LABEL,
        object_id=widget.pk,
        range_key="1d",
        anchor=anchor,
    )

    assert granularity == "hour"
    by_bucket = {row["bucket_start"]: row for row in rows}
    current = by_bucket[anchor]
    assert current["distinct_users"] == 2
    assert current["presence_hours"] == round((1800 + 900) / 3600, 2)
    previous = by_bucket[previous_hour]
    assert previous["distinct_users"] == 1
    assert previous["presence_hours"] == 1.0


# --- Required test 5: retention removes rows outside the window, keeps rows inside ---


@pytest.mark.django_db
def test_retention_removes_old_rows_and_keeps_recent_rows(user, widget, content_type):
    now = timezone.now()
    old = _make_bucket(
        content_type=content_type, object_id=widget.pk, user=user,
        bucket_start=now - datetime.timedelta(days=400), active_seconds=10,
    )
    recent = _make_bucket(
        content_type=content_type, object_id=widget.pk, user=user,
        bucket_start=now - datetime.timedelta(days=10), active_seconds=10,
    )

    call_command("cleanup_activity_buckets", "--older-than-days", "365")

    assert not ActivityBucket.objects.filter(pk=old.pk).exists()
    assert ActivityBucket.objects.filter(pk=recent.pk).exists()


# --- Required test 6: a caller without read permission gets nothing ---


@pytest.mark.django_db
def test_query_denies_read_without_registered_policy_default_closed(user, widget):
    # No policy registered at all for this app_key -- must deny, not fall
    # through to "any authenticated user may read." If the permission check
    # were dropped, this would return rows instead of raising.
    with pytest.raises(ActivityPermissionDenied):
        query_activity(
            actor=user,
            app_key="never-registered-app",
            content_type_label=CONTENT_TYPE_LABEL,
            object_id=widget.pk,
            range_key="1d",
        )


@pytest.mark.django_db
def test_query_denies_read_when_policy_explicitly_denies(user, widget):
    register_activity_policy("deny-app", DenyPolicy())
    try:
        with pytest.raises(ActivityPermissionDenied):
            query_activity(
                actor=user,
                app_key="deny-app",
                content_type_label=CONTENT_TYPE_LABEL,
                object_id=widget.pk,
                range_key="1d",
            )
    finally:
        unregister_activity_policy("deny-app")


@pytest.mark.django_db
def test_query_allows_read_when_policy_permits(user, widget, content_type, allow_policy):
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=timezone.now().replace(minute=0, second=0, microsecond=0), active_seconds=60)

    rows, _granularity = query_activity(
        actor=user,
        app_key=APP_KEY,
        content_type_label=CONTENT_TYPE_LABEL,
        object_id=widget.pk,
        range_key="1d",
    )

    assert len(rows) == 1


# --- Anchoring fallback chain ---


@pytest.mark.django_db
def test_anchor_falls_back_to_most_recent_bucket_then_now(user, widget, content_type, allow_policy):
    latest = datetime.datetime(2026, 1, 10, 5, 0, tzinfo=datetime.timezone.utc)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=latest, active_seconds=60)
    _make_bucket(
        content_type=content_type, object_id=widget.pk, user=user,
        bucket_start=latest - datetime.timedelta(days=5), active_seconds=60,
    )

    # No anchor supplied -> derive from MAX(bucket_start) for the scope.
    rows, _granularity = query_activity(
        actor=user, app_key=APP_KEY, content_type_label=CONTENT_TYPE_LABEL,
        object_id=widget.pk, range_key="1d",
    )
    assert any(row["bucket_start"] == latest for row in rows)


@pytest.mark.django_db
def test_supplied_anchor_takes_precedence_over_derived(user, widget, content_type, allow_policy):
    latest = datetime.datetime(2026, 1, 10, 5, 0, tzinfo=datetime.timezone.utc)
    old = datetime.datetime(2025, 6, 1, 5, 0, tzinfo=datetime.timezone.utc)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=latest, active_seconds=60)
    _make_bucket(content_type=content_type, object_id=widget.pk, user=user, bucket_start=old, active_seconds=120)

    # An explicit anchor near `old` must not pull in `latest`, which lies
    # outside the resulting window -- proves the app-supplied anchor wins.
    rows, _granularity = query_activity(
        actor=user, app_key=APP_KEY, content_type_label=CONTENT_TYPE_LABEL,
        object_id=widget.pk, range_key="1d", anchor=old,
    )
    assert all(row["bucket_start"] != latest for row in rows)
    assert any(row["bucket_start"] == old for row in rows)
