"""Per-user period cost limit, admin overrides and the per-entry cost estimate (DCM-CHAT-16)."""
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from .models import AssistantConfig, AssistantSummary, AssistantTurn

# A turn that failed with one of these codes never reached the provider: it cost nothing.
PRE_PROVIDER_CODES = {"not_configured", "invalid_selection", "access_lost", "archived", "rate_limited",
                      "turn_active", "quota_exceeded"}
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
ESTIMATE_MIN_ANSWERS = 10
ESTIMATE_DAYS = 30
ESTIMATE_CACHE_SECONDS = 600


def limit_setting_errors(value):
    """Errors in DCM_ASSISTANT_COST_LIMIT (absent means no limit)."""
    if value is None:
        return []
    if not isinstance(value, dict) or set(value) != {"period", "usd_micros"}:
        return ["DCM_ASSISTANT_COST_LIMIT must contain exactly period and usd_micros."]
    if value["period"] not in {"day", "month"}:
        return ["DCM_ASSISTANT_COST_LIMIT period must be day or month."]
    if type(value["usd_micros"]) is not int or value["usd_micros"] < 0:
        return ["DCM_ASSISTANT_COST_LIMIT usd_micros must be a non-negative integer."]
    return []


FAIL_CLOSED = {"period": "day", "usd_micros": 0}


def limit_setting():
    value = getattr(settings, "DCM_ASSISTANT_COST_LIMIT", None)
    if value is None:
        return None
    # a malformed limit is not "no limit": every turn is refused until the setting is fixed (E002)
    return FAIL_CLOSED if limit_setting_errors(value) else value


def unknown_turn_cost():
    value = getattr(settings, "DCM_ASSISTANT_UNKNOWN_TURN_COST_USD_MICROS", 50_000)
    return value if type(value) is int and value >= 0 else 50_000


def stored_config():
    """The admin overrides, or None; reading never creates the row."""
    return AssistantConfig.objects.filter(pk=1).first()


def save_config(**fields):
    row, _ = AssistantConfig.objects.get_or_create(pk=1)
    for key, value in fields.items():
        setattr(row, key, value)
    row.save()
    return row


def disabled_entry_ids():
    row = stored_config()
    return set(row.disabled_entries or []) if row else set()


def stored_default_entry():
    row = stored_config()
    return row.default_entry if row else ""


def effective_limit():
    conf = limit_setting()
    if conf is None:
        return None
    row = stored_config()
    return row.limit_usd_micros if row and row.limit_usd_micros is not None else conf["usd_micros"]


def period_window(period, now=None):
    """Start and end of the current period in the server's TIME_ZONE."""
    local_now = timezone.localtime(now or timezone.now())
    day = local_now.date()
    if period == "day":
        start_date, end_date = day, day + timedelta(days=1)
    else:
        start_date = day.replace(day=1)
        end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(start_date, time.min), tz),
            timezone.make_aware(datetime.combine(end_date, time.min), tz))


def _reported(usage):
    return any(int((usage or {}).get(key, 0) or 0) > 0 for key in TOKEN_KEYS)


def average_cost_usd_micros(entry_id):
    """Mean cost per answer of the last 30 days for one entry, None below 10 answers; cached."""
    key = f"dcm-assistant-average-cost:{entry_id}"
    cached = cache.get(key, "missing")
    if cached != "missing":
        return cached
    driver, _, model = entry_id.partition(":")
    since = timezone.now() - timedelta(days=ESTIMATE_DAYS)
    costs = [cost for cost, usage in AssistantTurn.objects.filter(driver=driver, model=model, state="completed",
             created_at__gte=since).values_list("cost_usd_micros", "usage") if _reported(usage)]
    value = None
    if len(costs) >= ESTIMATE_MIN_ANSWERS:
        value = int((Decimal(sum(costs)) / len(costs)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    cache.set(key, value, ESTIMATE_CACHE_SECONDS)
    return value


def _turn_cost(turn):
    if _reported(turn.usage) or turn.cost_usd_micros > 0:
        return int(turn.cost_usd_micros or 0)
    if turn.state in {"failed", "cancelled", "interrupted"} and turn.error_code not in PRE_PROVIDER_CODES:
        average = average_cost_usd_micros(f"{turn.driver}:{turn.model}")
        return average if average is not None else unknown_turn_cost()
    return 0


def used_usd_micros(user, start, end):
    turns = AssistantTurn.objects.filter(Q(sender=user) | Q(sender__isnull=True, chat__owner=user),
                                         created_at__gte=start, created_at__lt=end)
    total = sum(_turn_cost(turn) for turn in turns.only(
        "driver", "model", "state", "error_code", "usage", "cost_usd_micros"))
    summaries = AssistantSummary.objects.filter(chat__owner=user, created_at__gte=start, created_at__lt=end)
    return total + sum(int(cost or 0) for cost in summaries.values_list("cost_usd_micros", flat=True))


def limit_state(user, now=None):
    conf = limit_setting()
    if conf is None:
        return {"period": None, "limit_usd_micros": None, "used_usd_micros": None, "resets_at": None}
    start, end = period_window(conf["period"], now)
    return {"period": conf["period"], "limit_usd_micros": effective_limit(),
            "used_usd_micros": used_usd_micros(user, start, end), "resets_at": end.isoformat()}


def over_limit(user):
    state = limit_state(user)
    return state["limit_usd_micros"] is not None and state["used_usd_micros"] >= state["limit_usd_micros"]
