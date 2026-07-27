"""Domain-free date resolution and visibility rules for provider-derived todos."""
import calendar
import re
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.conf import settings


TODO_TIMEZONE = ZoneInfo(getattr(settings, "TODO_ENGINE_TIMEZONE", "UTC"))

ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?)?$",
    re.IGNORECASE,
)
# Non-greedy base matching keeps the final +/-P... suffix distinct while still
# allowing provider-defined hyphenated base names when no duration is present.
DUE_EXPRESSION_RE = re.compile(r"^([\w.-]+?)(?:([+-])(P.*))?$", re.IGNORECASE)


def _to_aware(value, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=tz)
    return None


def _parse_duration(duration: str | None) -> dict[str, int] | None:
    if not duration or duration == "P0D":
        return {"years": 0, "months": 0, "weeks": 0, "days": 0, "hours": 0, "minutes": 0}
    match = ISO_DURATION_RE.match(duration)
    if not match:
        return None
    return {name: int(value or "0") for name, value in match.groupdict().items()}


def _shift_months(value: datetime, months_delta: int) -> datetime:
    month_index = value.month - 1 + months_delta
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def shift_date_by_duration(value, duration: str | None, direction: int = 1, *, tz: ZoneInfo = TODO_TIMEZONE) -> datetime | None:
    """Shift a date/datetime by the supported ISO-8601 duration components."""

    dt = _to_aware(value, tz)
    parts = _parse_duration(duration)
    if dt is None or parts is None:
        return None
    shifted = dt
    if parts["years"]:
        try:
            shifted = shifted.replace(year=shifted.year + parts["years"] * direction)
        except ValueError:  # Leap day moving to a non-leap year.
            shifted = shifted.replace(year=shifted.year + parts["years"] * direction, day=28)
    if parts["months"]:
        shifted = _shift_months(shifted, parts["months"] * direction)
    return shifted + timedelta(
        days=(parts["weeks"] * 7 + parts["days"]) * direction,
        hours=parts["hours"] * direction,
        minutes=parts["minutes"] * direction,
    )


def resolve_due_date(expression, due_base_resolver, tz: ZoneInfo = TODO_TIMEZONE) -> datetime | None:
    if not expression:
        return None
    match = DUE_EXPRESSION_RE.match(expression)
    if not match:
        return None
    base, operator, duration = match.groups()
    due_base = _to_aware(due_base_resolver(base), tz)
    if due_base is None or not operator or not duration:
        return due_base
    return shift_date_by_duration(due_base, duration, 1 if operator == "+" else -1, tz=tz)


def should_include_task(due_at, config: dict, now: datetime) -> bool:
    if config.get("alwaysVisible"):
        return True
    if due_at is None:
        return True
    if not config.get("persistUntilDone"):
        visible_until = shift_date_by_duration(due_at, "P3D", 1)
        if visible_until is not None and now > visible_until:
            return False
    remind_before = config.get("remindBefore")
    if not remind_before:
        return now >= due_at
    visible_from = shift_date_by_duration(due_at, remind_before, -1)
    return now >= visible_from if visible_from is not None else True


def _start_of_day(value, tz: ZoneInfo) -> datetime | None:
    dt = _to_aware(value, tz)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0) if dt is not None else None


def resolve_task_severity(default_severity, due_at, now: datetime, tz: ZoneInfo = TODO_TIMEZONE) -> str:
    if due_at is None:
        return default_severity or "low"
    due_day = _start_of_day(due_at, tz) or due_at
    today = _start_of_day(now, tz) or now
    if due_day < today:
        return "overdue"
    return default_severity or "low"


def format_due(due_at, has_due_time: bool, tz: ZoneInfo = TODO_TIMEZONE) -> str | None:
    dt = _to_aware(due_at, tz)
    if dt is None:
        return None
    if has_due_time:
        return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return dt.astimezone(tz).date().isoformat()


def clean_output(item: dict, omit_keys: frozenset[str] = frozenset()) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_") and key not in omit_keys}


def materialize_todo(content: dict, config: dict, now: datetime, *, due_base_resolver, has_due_time: bool, tz: ZoneInfo = TODO_TIMEZONE, omit_keys: frozenset[str] = frozenset()) -> dict | None:
    due_at = resolve_due_date(config.get("due"), due_base_resolver, tz)
    if not should_include_task(due_at, config, now):
        return None
    return clean_output(
        {
            **content,
            "due": format_due(due_at, has_due_time, tz),
            "severity": resolve_task_severity(config.get("severity"), due_at, now, tz),
        },
        omit_keys,
    )
