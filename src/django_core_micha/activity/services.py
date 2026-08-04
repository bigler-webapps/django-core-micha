from __future__ import annotations

import datetime

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, F, Max, Sum
from django.db.models.functions import TruncDay, TruncHour, TruncMonth
from django.utils import timezone

from .models import ActivityBucket, floor_to_bucket_start
from .policy import get_activity_policy


class ActivityPermissionDenied(PermissionError):
    """Raised when the registered ActivityPolicy denies a read."""


class ActivityValidationError(ValueError):
    """Raised for a malformed ping/query request — callers map this to 400."""


# range -> (window, granularity). Fixed platform-wide mapping (operator, ACT-1) —
# the resolution follows the window, it is not an independent caller-supplied
# parameter, precisely so a request can never ask for an unreadable point count.
RANGE_CONFIG = {
    "1d": (datetime.timedelta(days=1), "hour"),
    "1w": (datetime.timedelta(weeks=1), "4hour"),
    "1m": (datetime.timedelta(days=30), "day"),
    "1y": (datetime.timedelta(days=365), "month"),
}

DEFAULT_HEARTBEAT_MAX_CREDIT_SECONDS = 45


def _resolve_content_type(*, content_type_label: str) -> ContentType:
    try:
        app_label, model = content_type_label.split(".", 1)
    except (ValueError, AttributeError) as exc:
        raise ActivityValidationError(
            "content_type must be an 'app_label.model' string."
        ) from exc
    try:
        return ContentType.objects.get_by_natural_key(app_label, model)
    except ContentType.DoesNotExist as exc:
        raise ActivityValidationError(f"Unknown content_type {content_type_label!r}.") from exc


def record_ping(*, actor, app_key: str, content_type_label: str, object_id: str) -> None:
    """Upsert the current-hour bucket for (app_key, scope, actor), accumulating
    active_seconds by a capped delta since the actor's last ping.

    Ported from jg-ferien's EventActivityPingView (events/views.py:322-361) —
    three details matter and must not be dropped:
    - select_for_update() prevents double-crediting from concurrent tabs pinging
      at once.
    - The delta is capped (default 45s), not the raw elapsed time — an
      uncapped delta would let a long gap (a laptop asleep for hours, a stale
      background tab) inflate active_seconds far beyond real presence.
    - The final increment is a single .update() with an F() expression, not a
      read-modify-.save() race.
    """
    content_type = _resolve_content_type(content_type_label=content_type_label)
    now = timezone.now()
    bucket_start = floor_to_bucket_start(now)
    max_credit_seconds = max(
        0,
        int(getattr(settings, "ACTIVITY_HEARTBEAT_MAX_CREDIT_SECONDS", DEFAULT_HEARTBEAT_MAX_CREDIT_SECONDS)),
    )

    with transaction.atomic():
        last_ping_at = (
            ActivityBucket.objects.select_for_update()
            .filter(
                app_key=app_key,
                content_type=content_type,
                object_id=str(object_id),
                user=actor,
                last_ping_at__isnull=False,
            )
            .order_by("-last_ping_at")
            .values_list("last_ping_at", flat=True)
            .first()
        )
        delta_seconds = 0
        if last_ping_at is not None:
            elapsed_seconds = (now - last_ping_at).total_seconds()
            delta_seconds = int(max(0, min(max_credit_seconds, elapsed_seconds)))
        bucket, _created = ActivityBucket.objects.get_or_create(
            app_key=app_key,
            content_type=content_type,
            object_id=str(object_id),
            user=actor,
            bucket_start=bucket_start,
        )
        ActivityBucket.objects.filter(pk=bucket.pk).update(
            active_seconds=F("active_seconds") + delta_seconds,
            last_ping_at=now,
        )


_NATIVE_TRUNC = {
    "hour": lambda: TruncHour("bucket_start", tzinfo=datetime.timezone.utc),
    "day": lambda: TruncDay("bucket_start", tzinfo=datetime.timezone.utc),
    "month": lambda: TruncMonth("bucket_start", tzinfo=datetime.timezone.utc),
}


def _floor_to_4hour(moment):
    return moment.replace(hour=(moment.hour // 4) * 4, minute=0, second=0, microsecond=0)


def _aggregate_native(queryset, granularity):
    """hour/day/month all have a native Django Trunc* function — aggregate
    directly in the database in one query, one row per distinct bucket."""
    rows = (
        queryset.annotate(bucket=_NATIVE_TRUNC[granularity]())
        .values("bucket")
        .annotate(distinct_users=Count("user", distinct=True), total_active_seconds=Sum("active_seconds"))
        .order_by("bucket")
    )
    return [
        {"bucket_start": row["bucket"], "distinct_users": row["distinct_users"], "total_active_seconds": row["total_active_seconds"] or 0}
        for row in rows
    ]


def _aggregate_4hour(queryset):
    """Django has no built-in TruncXHours for an arbitrary N (verified: dcm has
    no prior TruncHour/TruncDay/TruncMonth usage anywhere to copy, and a
    hand-built `TruncDay(...) + int_expr * timedelta` ORM expression does not
    reliably combine across backends — confirmed broken on sqlite in this
    package's own tests).

    Aggregate to the hour first — a real, native, database-side GROUP BY — then
    combine into 4-hour windows in Python. This is bounded and not equivalent
    to shipping raw per-ping rows: the 1-hour store already means at most one
    row per user per hour, and this granularity only ever serves the 1-week
    range (RANGE_CONFIG), so the hour-level aggregate is at most ~168 rows per
    user, never the ~2190-point year-at-raw-storage case this WO exists to
    avoid (a 1-year request uses native TruncMonth instead, see
    `_aggregate_native`). Distinct-user counts cannot be summed across the
    hourly rows without double-counting a user present in two hours of the same
    4-hour window, so per-(hour, user) rows are fetched (still DB-aggregated on
    active_seconds) and unioned into a user-id set per 4-hour window.
    """
    per_hour_user = (
        queryset.annotate(bucket=TruncHour("bucket_start", tzinfo=datetime.timezone.utc))
        .values("bucket", "user_id")
        .annotate(active_seconds=Sum("active_seconds"))
    )
    grouped: dict = {}
    for row in per_hour_user:
        block_start = _floor_to_4hour(row["bucket"])
        entry = grouped.setdefault(block_start, {"users": set(), "total_active_seconds": 0})
        entry["users"].add(row["user_id"])
        entry["total_active_seconds"] += row["active_seconds"] or 0
    return [
        {"bucket_start": block_start, "distinct_users": len(entry["users"]), "total_active_seconds": entry["total_active_seconds"]}
        for block_start, entry in sorted(grouped.items())
    ]


def resolve_anchor(*, supplied_anchor, app_key: str, content_type: ContentType, object_id: str):
    """anchor = supplied_anchor or MAX(bucket_start) for the scope or now.

    One expression, three fallbacks — per the WO, if this needs to grow beyond
    that, anchoring should be dropped entirely rather than grown.
    """
    if supplied_anchor is not None:
        return supplied_anchor
    derived = ActivityBucket.objects.filter(
        app_key=app_key, content_type=content_type, object_id=str(object_id)
    ).aggregate(latest=Max("bucket_start"))["latest"]
    return derived or timezone.now()


def query_activity(
    *,
    actor,
    app_key: str,
    content_type_label: str,
    object_id: str,
    range_key: str,
    anchor=None,
):
    """Return rolled-up activity rows for a scope, gated by the registered
    ActivityPolicy — reading is a privacy surface (per-user presence), unlike
    recording, which is always the actor's own.
    """
    if range_key not in RANGE_CONFIG:
        raise ActivityValidationError(
            f"range must be one of {sorted(RANGE_CONFIG)}, got {range_key!r}."
        )
    content_type = _resolve_content_type(content_type_label=content_type_label)

    try:
        policy = get_activity_policy(app_key)
    except LookupError as exc:
        raise ActivityPermissionDenied(
            f"No activity policy registered for {app_key!r} — denying by default."
        ) from exc
    if not policy.can_read_activity(
        actor=actor, app_key=app_key, content_type=content_type, object_id=str(object_id)
    ):
        raise ActivityPermissionDenied("Not permitted to read activity for this scope.")

    window, granularity = RANGE_CONFIG[range_key]
    resolved_anchor = resolve_anchor(
        supplied_anchor=anchor, app_key=app_key, content_type=content_type, object_id=object_id
    )
    range_from = resolved_anchor - window

    queryset = ActivityBucket.objects.filter(
        app_key=app_key,
        content_type=content_type,
        object_id=str(object_id),
        bucket_start__gte=range_from,
        bucket_start__lte=resolved_anchor,
    )
    rows = _aggregate_4hour(queryset) if granularity == "4hour" else _aggregate_native(queryset, granularity)

    result = [
        {
            "bucket_start": row["bucket_start"],
            "distinct_users": row["distinct_users"],
            "presence_hours": round(row["total_active_seconds"] / 3600, 2),
        }
        for row in rows
    ]
    return result, granularity
