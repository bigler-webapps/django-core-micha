from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from django_core_micha.notifications.todo.engine import (
    _parse_duration,
    clean_output,
    format_due,
    resolve_due_date,
    resolve_task_severity,
    shift_date_by_duration,
    should_include_task,
)


UTC = ZoneInfo("UTC")


def test_parse_duration_accepts_components_zero_and_rejects_invalid_values():
    assert _parse_duration("P1Y2M3W4DT5H6M") == {
        "years": 1, "months": 2, "weeks": 3, "days": 4, "hours": 5, "minutes": 6,
    }
    assert _parse_duration("P0D") == {"years": 0, "months": 0, "weeks": 0, "days": 0, "hours": 0, "minutes": 0}
    assert _parse_duration("tomorrow") is None


def test_shift_date_by_duration_handles_components_directions_and_month_end_clamping():
    start = datetime(2024, 1, 31, 10, 30, tzinfo=UTC)
    assert shift_date_by_duration(start, "P1M", tz=UTC) == datetime(2024, 2, 29, 10, 30, tzinfo=UTC)
    assert shift_date_by_duration(start, "P1M", -1, tz=UTC) == datetime(2023, 12, 31, 10, 30, tzinfo=UTC)
    assert shift_date_by_duration(start, "P1Y2M3W4DT5H6M", tz=UTC) == datetime(2025, 4, 25, 15, 36, tzinfo=UTC)


def test_resolve_due_date_supports_bare_plus_minus_and_date_bases():
    bases = {"start": date(2026, 5, 10)}
    resolver = bases.get
    assert resolve_due_date("start", resolver, UTC) == datetime(2026, 5, 10, tzinfo=UTC)
    assert resolve_due_date("start+P2D", resolver, UTC) == datetime(2026, 5, 12, tzinfo=UTC)
    assert resolve_due_date("start-P2D", resolver, UTC) == datetime(2026, 5, 8, tzinfo=UTC)
    assert resolve_due_date("bad expression!", resolver, UTC) is None


def test_should_include_task_honours_visibility_grace_persistence_and_lead_window():
    due = datetime(2026, 5, 10, tzinfo=UTC)
    assert should_include_task(due, {"alwaysVisible": True}, datetime(2026, 1, 1, tzinfo=UTC))
    assert not should_include_task(due, {}, datetime(2026, 5, 14, tzinfo=UTC))
    assert should_include_task(due, {"persistUntilDone": True}, datetime(2026, 5, 14, tzinfo=UTC))
    assert not should_include_task(due, {"remindBefore": "P3D"}, datetime(2026, 5, 6, tzinfo=UTC))
    assert should_include_task(due, {"remindBefore": "P3D"}, datetime(2026, 5, 7, tzinfo=UTC))


def test_severity_due_formatting_and_clean_output_are_timezone_aware():
    due = datetime(2026, 5, 10, 15, 4, tzinfo=timezone.utc)
    assert resolve_task_severity("medium", due, datetime(2026, 5, 11, tzinfo=UTC), UTC) == "overdue"
    assert resolve_task_severity(None, None, datetime(2026, 5, 11, tzinfo=UTC), UTC) == "low"
    assert format_due(due, False, UTC) == "2026-05-10"
    assert format_due(due, True, UTC) == "2026-05-10T15:04:00.000Z"
    assert clean_output({"title": "x", "_internal": 1, "omit": 2}, frozenset({"omit"})) == {"title": "x"}
