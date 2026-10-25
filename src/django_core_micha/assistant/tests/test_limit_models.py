from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.models import AssistantChat, AssistantConfig, AssistantSummary, AssistantTurn

pytestmark = pytest.mark.django_db

MINI, HAIKU = "openai_api:gpt-4o-mini", "anthropic_api:claude-haiku-5-5"
CATALOGUE = [
    {"driver": "openai_api", "model": "gpt-4o-mini", "name": "Mini", "efforts": ["low", "medium"], "billing": "pay_per_use", "default": True},
    {"driver": "anthropic_api", "model": "claude-haiku-5-5", "name": "Haiku", "efforts": ["low"], "billing": "pay_per_use"},
]
LIMIT = {"period": "month", "usd_micros": 100}
TOKENS = {"input_tokens": 5, "output_tokens": 5}


@pytest.fixture(autouse=True)
def assistant_settings():
    cache.clear()
    with override_settings(DCM_ASSISTANT_CATALOGUE=CATALOGUE, DCM_ASSISTANT_COST_LIMIT=LIMIT,
                           OPENAI_API_KEY="key", ANTHROPIC_API_KEY="key"):
        yield
    cache.clear()


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="limit-user", password="secret")


@pytest.fixture
def admin():
    return get_user_model().objects.create_user(username="limit-admin", password="secret", is_staff=True)


def _chat(user, entry=MINI, effort="low"):
    driver, model = entry.split(":")
    return AssistantChat.objects.create(owner=user, selected_driver=driver, selected_model=model, selected_effort=effort)


def _turn(user, chat, *, cost=0, usage=None, state="completed", error="", created=None, sender=True):
    row = AssistantTurn.objects.create(chat=chat, sender=user if sender else None, sequence=chat.turns.count() + 1,
        user_text="q", driver=chat.selected_driver, model=chat.selected_model, state=state,
        cost_usd_micros=cost, usage=usage or {}, error_code=error)
    if created is not None:
        AssistantTurn.objects.filter(pk=row.pk).update(created_at=created)
    return row


def _call(view, method, user, data=None):
    request = getattr(APIRequestFactory(), method)("/", data or {}, format="json")
    force_authenticate(request, user=user)
    return view.as_view()(request)


def _start(chat, user):
    from django_core_micha.assistant.turns import _start
    return _start(chat.pk, user.pk, "hello")


# 1. period limit

def test_turn_at_the_limit_is_refused_previous_period_free_and_admin_change_applies(user, admin):
    from django_core_micha.assistant.views import AdminLimitView
    from django_core_micha.assistant.limit import limit_state
    chat = _chat(user)
    _turn(user, chat, cost=500, usage=TOKENS, created=timezone.now() - timedelta(days=40))  # previous period only
    assert limit_state(user)["used_usd_micros"] == 0 and _start(_chat(user), user).pk
    _turn(user, chat, cost=60, usage=TOKENS)
    AssistantSummary.objects.create(chat=chat, up_to_sequence=1, text="s", cost_usd_micros=40)
    assert limit_state(user)["used_usd_micros"] == 100
    other = _chat(user)
    with pytest.raises(DriverError) as error:
        _start(other, user)
    assert error.value.code == "quota_exceeded" and not other.turns.exists()
    assert _call(AdminLimitView, "patch", user, {"limit_usd_micros": 1000}).status_code == 403
    response = _call(AdminLimitView, "patch", admin, {"limit_usd_micros": 1000})
    assert response.status_code == 200 and response.data == {"period": "month", "limit_usd_micros": 1000,
        "default_limit_usd_micros": 100, "override": True}
    assert _start(other, user).pk
    assert _call(AdminLimitView, "patch", admin, {"limit_usd_micros": None}).data["limit_usd_micros"] == 100
    assert _call(AdminLimitView, "patch", admin, {"limit_usd_micros": -1}).status_code == 400
    assert _call(AdminLimitView, "patch", admin, {"period": "day"}).status_code == 400


def test_limit_endpoint_reports_usage_limit_and_local_reset(user):
    from django_core_micha.assistant.views import UserLimitView
    _turn(user, _chat(user), cost=30, usage=TOKENS)
    zurich = ZoneInfo("Europe/Zurich")
    with override_settings(TIME_ZONE="Europe/Zurich"):
        timezone.activate(zurich)
        try:
            response = _call(UserLimitView, "get", user)
            assert response.data["used_usd_micros"] == 30 and response.data["limit_usd_micros"] == 100
            resets = datetime.fromisoformat(response.data["resets_at"]).astimezone(zurich)
            assert (resets.day, resets.hour, resets.minute) == (1, 0, 0) and response["Cache-Control"] == "no-store"
            with override_settings(DCM_ASSISTANT_COST_LIMIT={"period": "day", "usd_micros": 5}):
                day = _call(UserLimitView, "get", user).data
                local = datetime.fromisoformat(day["resets_at"]).astimezone(zurich)
                assert day["period"] == "day" and (local.hour, local.minute) == (0, 0)
                assert local.date() == timezone.localtime().date() + timedelta(days=1)
        finally:
            timezone.deactivate()
    with override_settings(DCM_ASSISTANT_COST_LIMIT=None):
        assert _call(UserLimitView, "get", user).data == {"period": None, "limit_usd_micros": None,
            "used_usd_micros": None, "resets_at": None}


def test_unknown_costs_count_at_average_or_fallback_and_pre_provider_failures_are_free(user):
    from django_core_micha.assistant.limit import limit_state
    chat = _chat(user)
    _turn(user, chat, state="failed", error="provider_error")          # unknown: fallback
    _turn(user, chat, state="failed", error="not_configured")          # never reached the provider
    _turn(user, chat, state="completed")                                # completed without usage: zero
    with override_settings(DCM_ASSISTANT_UNKNOWN_TURN_COST_USD_MICROS=7):
        assert limit_state(user)["used_usd_micros"] == 7
        for _ in range(10):
            _turn(user, chat, cost=20, usage=TOKENS)
        cache.clear()
        assert limit_state(user)["used_usd_micros"] == 20 + 200  # the unknown turn now counts at the 30-day average


def test_no_limit_setting_means_no_limit_and_admin_limit_is_not_configured(user, admin):
    from django_core_micha.assistant.views import AdminLimitView
    chat = _chat(user)
    _turn(user, chat, cost=10_000_000, usage=TOKENS)
    with override_settings(DCM_ASSISTANT_COST_LIMIT=None):
        assert _start(_chat(user), user).pk
        assert _call(AdminLimitView, "get", admin).status_code == 404
    assert not AssistantConfig.objects.exists()  # reading never created the row


def test_malformed_limit_setting_is_a_system_check_error():
    from django.core import checks
    with override_settings(DCM_ASSISTANT_COST_LIMIT={"period": "week", "usd_micros": 1}):
        assert "dcm_assistant.E002" in {message.id for message in checks.run_checks()}
    assert "dcm_assistant.E002" not in {message.id for message in checks.run_checks()}


# 2. model enablement

def test_disabled_entry_is_hidden_listed_for_admins_and_chat_continues_on_default(user, admin):
    from django_core_micha.assistant.views import AdminModelsView, CatalogueView
    assert _call(AdminModelsView, "patch", user, {"enabled": [MINI]}).status_code == 403
    assert _call(AdminModelsView, "get", user).status_code == 403
    response = _call(AdminModelsView, "patch", admin, {"enabled": [MINI], "default": MINI})
    assert response.status_code == 200 and response.data["default"] == MINI
    assert {row["id"]: row["enabled"] for row in response.data["entries"]} == {MINI: True, HAIKU: False}
    assert [row["id"] for row in _call(CatalogueView, "get", user).data["entries"]] == [MINI]
    chat = _chat(user, HAIKU)
    turn = _start(chat, user)
    chat.refresh_from_db()
    assert (turn.driver, turn.model, chat.selected_model, chat.selected_effort) == ("openai_api", "gpt-4o-mini", "gpt-4o-mini", "low")


def test_removed_entry_continues_on_default(user):
    chat = AssistantChat.objects.create(owner=user, selected_driver="openai_api", selected_model="gone", selected_effort="max")
    turn = _start(chat, user)
    assert (turn.model, turn.effort) == ("gpt-4o-mini", "low")


def test_nothing_enabled_is_unavailable(user, admin):
    from django_core_micha.assistant.views import AdminModelsView, AvailabilityView, CatalogueView
    assert _call(AvailabilityView, "get", user).data["status"] == "available"
    assert _call(AdminModelsView, "patch", admin, {"enabled": [], "default": ""}).status_code == 200
    status = _call(AvailabilityView, "get", user).data
    assert status["status"] == "unavailable" and status["reason"] == "no_model_enabled" and "providers" in status
    assert _call(CatalogueView, "get", user).data == {"default": None, "entries": []}
    with pytest.raises(DriverError) as error:
        _start(_chat(user), user)
    assert error.value.code == "not_configured"


def test_disabling_the_default_without_a_new_one_is_refused(admin):
    from django_core_micha.assistant.views import AdminModelsView
    assert _call(AdminModelsView, "patch", admin, {"default": HAIKU}).status_code == 200
    response = _call(AdminModelsView, "patch", admin, {"enabled": [MINI]})
    assert response.status_code == 400 and response.data["code"] == "default_disabled"
    assert _call(AdminModelsView, "patch", admin, {"enabled": ["nope:x"]}).data["code"] == "invalid_selection"


def test_entry_added_to_settings_later_is_enabled(user, admin):
    from django_core_micha.assistant.views import AdminModelsView, CatalogueView
    _call(AdminModelsView, "patch", admin, {"enabled": [MINI], "default": MINI})
    extra = {"driver": "anthropic_api", "model": "claude-sonnet-5-5", "efforts": ["low"], "billing": "pay_per_use"}
    with override_settings(DCM_ASSISTANT_CATALOGUE=CATALOGUE + [extra]):
        ids = [row["id"] for row in _call(CatalogueView, "get", user).data["entries"]]
    assert ids == [MINI, "anthropic_api:claude-sonnet-5-5"]


# 3. estimate

def test_estimate_needs_ten_recent_answers_with_usage(user):
    from django_core_micha.assistant.limit import average_cost_usd_micros
    chat = _chat(user)
    for cost in range(1, 10):
        _turn(user, chat, cost=cost, usage=TOKENS)
    _turn(user, chat, cost=999, usage=TOKENS, created=timezone.now() - timedelta(days=31))
    _turn(user, chat, cost=0, state="completed")
    assert average_cost_usd_micros(MINI) is None
    cache.clear()
    _turn(user, chat, cost=12, usage=TOKENS)
    assert average_cost_usd_micros(MINI) == 6  # (1+...+9+12)/10 = 5.7, rounded half up


# 4. tool steps

def test_tool_steps_return_with_the_history(user):
    from django_core_micha.assistant.views import TurnsView
    chat = _chat(user)
    from django_core_micha.assistant.turns import _finish
    turn = _turn(user, chat, state="generating")
    assert _finish(turn.pk, "answer", "completed", usage=TOKENS, trace=[
        {"name": "search", "label": "Searching", "sources": [{"id": 1}]}, {"name": "read", "label": "Reading", "sources": []}])
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)
    row = TurnsView.as_view()(request, chat_id=chat.pk).data["results"][0]
    assert row["tool_steps"] == [{"name": "search", "label": "Searching"}, {"name": "read", "label": "Reading"}]
    assert row["sources"] == [{"id": 1}]


# 5. one-shot completions

def test_one_shot_completion_does_not_count(user, monkeypatch):
    from django_core_micha.assistant import completion
    from django_core_micha.assistant.limit import limit_state

    class Provider:
        async def stream(self, **kwargs):
            yield {"type": "delta", "text": "ok"}
            yield {"type": "usage", "usage": {"input_tokens": 1000, "output_tokens": 1000}}

    monkeypatch.setattr(completion, "resolve_credential", lambda *args: ("key", "deployment"))
    monkeypatch.setattr(completion, "_make_driver", lambda *args: Provider())
    before = limit_state(user)["used_usd_micros"]
    result = async_to_sync(completion.acomplete)(user, prompt="p", driver="openai_api", model="gpt-4o-mini")
    assert result.text == "ok" and result.usage["input_tokens"] == 1000
    assert limit_state(user)["used_usd_micros"] == before == 0


def test_entry_allowed_denying_everything_reports_no_model_enabled(user):
    from django_core_micha.assistant.views import AvailabilityView
    with override_settings(DCM_ASSISTANT_ENTRY_ALLOWED=lambda user, row: False):
        assert _call(AvailabilityView, "get", user).data["reason"] == "no_model_enabled"


def test_malformed_limit_fails_closed(user):
    with override_settings(DCM_ASSISTANT_COST_LIMIT={"period": "week", "usd_micros": 1}):
        with pytest.raises(DriverError) as error:
            _start(_chat(user), user)
    assert error.value.code == "quota_exceeded"
