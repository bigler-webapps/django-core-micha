from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from threading import Barrier, Lock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from django_core_micha.billing import annual_term_reminders as reminders
from django_core_micha.billing.models import AnnualTermReminder, Contract, ContractPayment

pytestmark = pytest.mark.django_db


def make_contract(*, app="app", status="active", phase="initial", language="de", end=None, amount=725, tokens=240):
    User = get_user_model()
    user = User.objects.create_user(username=f"owner-{Contract.objects.count()}-{app}", email="owner@example.test", password="x")
    now = timezone.now()
    end = end or now + timedelta(days=30)
    contract = Contract.objects.create(
        user=user, app=app, kind="subscription", plan_key="annual", stripe_object_id=f"sub-{user.pk}",
        started_at=end - timedelta(days=365), initial_term_end=end, continuation_plan_key="monthly",
        continuation_amount_cents=amount, continuation_token_allowance=tokens, continuation_interval="month",
        phase=phase, status=status, schedule_status="created",
    )
    ContractPayment.objects.create(contract=contract, amount_paid_cents=8000, currency="chf", billing_reason="initial")
    return contract


@pytest.fixture
def configured():
    with override_settings(
        DCM_BILLING_APP="app", DCM_BILLING_REMINDER_ENABLED=True,
        DCM_BILLING_REMINDER_LANGUAGE_FALLBACK="fr", DCM_BILLING_CANCELLATION_URL="https://example.test/cancel",
        MAILERS={"default": {"BACKEND": "anymail.backends.resend.EmailBackend"}}, ANYMAIL={"RESEND_API_KEY": "test-key"},
        DEFAULT_FROM_EMAIL="billing@example.test",
    ):
        yield


def test_exact_threshold_contract_terms_language_and_accepted_idempotence(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(language="unsupported", end=now + timedelta(days=30))
    requests = []
    monkeypatch.setattr(reminders, "_send_resend", lambda payload, key: (requests.append((payload, key)) or "email_1"))

    assert reminders.process_annual_term_reminders(now=now - timedelta(seconds=1), app="app")[0] == 0
    assert not AnnualTermReminder.objects.exists()
    assert reminders.process_annual_term_reminders(now=now, app="app")[0] == 1
    assert reminders.process_annual_term_reminders(now=now + timedelta(minutes=1), app="app")[0] == 0

    row = AnnualTermReminder.objects.get(contract=contract)
    payload, key = requests[0]
    assert row.status == "sent" and row.sent_at == now
    assert row.attempts == 1 and len(requests) == 1 and row.late is False
    assert key == row.idempotency_key
    assert payload["to"] == [contract.user.email]
    assert "7.25 CHF" in payload["text"]
    assert "240 jetons" in payload["text"]
    assert timezone.localtime(contract.initial_term_end).date().isoformat() in payload["text"]
    assert "cancel" in payload["text"]
    assert payload["subject"] == reminders._TEXT["fr"][0]


@pytest.mark.django_db(transaction=True)
def test_concurrent_workers_share_provider_identity(configured, monkeypatch):
    contract = make_contract()
    payload = reminders._payload(contract, "CHF")
    key = f"dcm-annual-reminder:{contract.app}:{contract.reference}"
    row = AnnualTermReminder.objects.create(
        contract=contract, idempotency_key=key, payload=payload,
        due_at=contract.initial_term_end - timedelta(days=30),
    )
    lock = Lock()
    requests = []
    accepted_keys = set()

    def fake_provider(sent_payload, idempotency_key):
        assert sent_payload == payload
        with lock:
            requests.append(idempotency_key)
        with lock:
            accepted_keys.add(idempotency_key)
        return "provider-idempotent-message"

    monkeypatch.setattr(reminders, "_send_resend", fake_provider)
    now = contract.initial_term_end - timedelta(days=29)
    with ThreadPoolExecutor(max_workers=2) as pool:
        from django.db import OperationalError
        def run(_worker):
            try:
                return reminders._process_one(row.pk, now)
            except OperationalError:
                return False  # SQLite rejects a competing writer; a later scheduler run retries.
        results = list(pool.map(run, range(2)))
    retry_result = reminders._process_one(row.pk, now + timedelta(minutes=2))
    row.refresh_from_db()
    assert requests == [key] and accepted_keys == {key} and row.status == "sent"
    assert sum(results) + bool(retry_result) == 1


def test_uses_contract_currency_and_never_catalogue_fallback(configured, monkeypatch):
    contract = make_contract(amount=12345, tokens=987)
    contract.payments.all().delete()
    calls = []
    monkeypatch.setattr(reminders, "_send_resend", lambda payload, key: (calls.append(payload) or "email_2"))
    now = contract.initial_term_end - timedelta(days=30)
    assert reminders.process_annual_term_reminders(now=now, app="app")[1] == 1
    assert AnnualTermReminder.objects.get(contract=contract).last_error_code == "missing_reminder_data"
    assert calls == []


def test_ambiguous_retry_keeps_same_payload_key_and_first_attempt_window(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    attempts = []

    def fake(payload, key):
        attempts.append((payload, key))
        if len(attempts) == 1:
            raise reminders.ProviderOutcome("timeout", ambiguous=True)
        return "email_accepted"

    monkeypatch.setattr(reminders, "_send_resend", fake)
    assert reminders.process_annual_term_reminders(now=now, app="app")[0] == 0
    row = AnnualTermReminder.objects.get(contract=contract)
    original_time, original_payload, original_key = row.first_attempt_at, row.payload, row.idempotency_key
    assert row.status == "ambiguous"
    assert reminders.process_annual_term_reminders(now=now + timedelta(hours=23), app="app")[0] == 1
    row.refresh_from_db()
    assert row.status == "sent" and row.first_attempt_at == original_time
    assert attempts[0] == attempts[1] == (original_payload, original_key)


def test_ambiguous_after_window_becomes_unresolved_without_resend(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    calls = []

    def ambiguous(payload, key):
        calls.append(key)
        raise reminders.ProviderOutcome("timeout", ambiguous=True)

    monkeypatch.setattr(reminders, "_send_resend", ambiguous)
    reminders.process_annual_term_reminders(now=now, app="app")
    row = AnnualTermReminder.objects.get(contract=contract)
    later = row.first_attempt_at + timedelta(hours=24)
    assert reminders.process_annual_term_reminders(now=later, app="app")[1] == 1
    row.refresh_from_db()
    assert row.status == "unresolved" and row.last_error_code == "idempotency_window_expired"
    assert calls == [row.idempotency_key]


def test_restart_with_sending_state_after_window_never_sends(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    row = AnnualTermReminder.objects.create(contract=contract, status="sending", idempotency_key="stale",
        payload=reminders._payload(contract, "CHF"), due_at=now - timedelta(days=1),
        first_attempt_at=now - timedelta(hours=25), last_attempt_at=now - timedelta(hours=25))
    monkeypatch.setattr(reminders, "_send_resend", lambda *a: pytest.fail("unsafe resend"))
    reminders.process_annual_term_reminders(now=now, app="app")
    row.refresh_from_db()
    assert row.status == "unresolved"


def test_definite_response_after_ambiguous_attempt_stays_ambiguous(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    row = AnnualTermReminder.objects.create(contract=contract, status="ambiguous", idempotency_key="ambiguous",
        payload=reminders._payload(contract, "CHF"), due_at=now - timedelta(days=1), first_attempt_at=now)
    def reject(*args):
        raise reminders.ProviderOutcome("http_400", ambiguous=False)
    monkeypatch.setattr(reminders, "_send_resend", reject)
    reminders._process_one(row.pk, now + timedelta(hours=1))
    row.refresh_from_db()
    assert row.status == "ambiguous"


def test_recipient_supported_language_overrides_fallback(configured):
    assert reminders._recipient_language(SimpleNamespace(contact_profile=SimpleNamespace(language="en"))) == "en"


def test_missing_continuation_schedule_does_not_promise_renewal(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    Contract.objects.filter(pk=contract.pk).update(schedule_status="failed")
    monkeypatch.setattr(reminders, "_send_resend", lambda *a: pytest.fail("schedule is not active"))
    reminders.process_annual_term_reminders(now=now, app="app")
    assert not AnnualTermReminder.objects.filter(contract=contract).exists()


def test_terms_changed_after_ambiguous_attempt_are_not_resent(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    monkeypatch.setattr(reminders, "_send_resend", lambda *a: (_ for _ in ()).throw(
        reminders.ProviderOutcome("timeout", ambiguous=True)))
    reminders.process_annual_term_reminders(now=now, app="app")
    row = AnnualTermReminder.objects.get(contract=contract)
    Contract.objects.filter(pk=contract.pk).update(continuation_amount_cents=999)
    monkeypatch.setattr(reminders, "_send_resend", lambda *a: pytest.fail("terms changed"))
    reminders._process_one(row.pk, now + timedelta(hours=1))
    row.refresh_from_db()
    assert row.status == "unresolved" and row.last_error_code == "reminder_terms_changed"


def test_live_clock_checks_window_again_after_waiting_for_contract(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    row = AnnualTermReminder.objects.create(contract=contract, status="ambiguous", idempotency_key="clock",
        payload=reminders._payload(contract, "CHF"), due_at=now, first_attempt_at=now - timedelta(hours=23))
    clock = iter([now, now + timedelta(hours=2)])
    monkeypatch.setattr(reminders.timezone, "now", lambda: next(clock, now + timedelta(hours=2)))
    monkeypatch.setattr(reminders, "_send_resend", lambda *a: pytest.fail("unsafe resend"))
    reminders._process_one(row.pk, now, live_clock=True)
    row.refresh_from_db()
    assert row.status == "unresolved"


def test_definite_failure_after_term_is_operator_visible(configured, monkeypatch):
    from io import StringIO
    from django_core_micha.billing.management.commands import billing_process_declarations as command_module
    now = timezone.now()
    contract = make_contract(end=now - timedelta(seconds=1))
    row = AnnualTermReminder.objects.create(contract=contract, status="failed", idempotency_key="failed",
        payload={}, due_at=now - timedelta(days=30))
    monkeypatch.setattr(command_module, "retry_declaration_emails", lambda: None)
    monkeypatch.setattr(command_module, "resume_withdrawals", lambda: 0)
    monkeypatch.setattr(command_module, "apply_due_cancellations", lambda now: 0)
    monkeypatch.setattr(command_module, "run_transition_guard", lambda now: 0)
    out = StringIO()
    call_command("billing_process_declarations", stdout=out)
    row.refresh_from_db()
    assert row.status == "unresolved"
    assert contract.reference in out.getvalue() and "initial_term_ended" in out.getvalue()


def test_withdrawal_between_ambiguous_attempts_cancels_retry(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    calls = []
    monkeypatch.setattr(reminders, "_send_resend", lambda payload, key: (calls.append(key) or (_ for _ in ()).throw(reminders.ProviderOutcome("timeout", ambiguous=True))))
    reminders.process_annual_term_reminders(now=now, app="app")
    row = AnnualTermReminder.objects.get(contract=contract)
    Contract.objects.filter(pk=contract.pk).update(status="withdrawn")
    assert reminders._process_one(row.pk, now + timedelta(hours=1)) is False
    row.refresh_from_db()
    assert row.status == "cancelled" and calls == [row.idempotency_key]


def test_definite_failure_retries_same_payload_until_term_end(configured, monkeypatch):
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=2))
    calls = []

    def failure(payload, key):
        calls.append(key)
        if len(calls) < 2:
            raise reminders.ProviderOutcome("http_400", ambiguous=False)
        return "accepted"

    monkeypatch.setattr(reminders, "_send_resend", failure)
    reminders.process_annual_term_reminders(now=now, app="app")
    row = AnnualTermReminder.objects.get(contract=contract)
    assert row.status == "failed"
    reminders.process_annual_term_reminders(now=now + timedelta(days=1), app="app")
    row.refresh_from_db()
    assert row.status == "sent" and row.attempts == 2 and calls == [row.idempotency_key] * 2


@pytest.mark.parametrize("status,phase", [("cancellation_scheduled", "initial"), ("withdrawn", "initial"), ("active", "continuation")])
def test_rechecks_contract_eligibility(configured, monkeypatch, status, phase):
    contract = make_contract(status=status, phase=phase)
    calls = []
    monkeypatch.setattr(reminders, "_send_resend", lambda payload, key: calls.append(key) or "sent")
    reminders.process_annual_term_reminders(now=contract.initial_term_end - timedelta(days=30), app="app")
    assert calls == []
    assert not AnnualTermReminder.objects.filter(contract=contract).exists()


def test_app_isolation_missed_schedule_and_late_state(configured, monkeypatch):
    now = timezone.now()
    late = make_contract(app="app", end=now + timedelta(days=29))
    other = make_contract(app="other", end=now + timedelta(days=29))
    payloads = []
    monkeypatch.setattr(reminders, "_send_resend", lambda payload, key: (payloads.append(payload) or "accepted"))
    reminders.process_annual_term_reminders(now=now, app="app")
    assert AnnualTermReminder.objects.filter(contract=late, late=True, status="sent").exists()
    assert not AnnualTermReminder.objects.filter(contract=other).exists()
    assert len(payloads) == 1 and payloads[0]["to"] == [late.user.email]


def test_provider_uses_http_idempotency_header_and_rejects_unsafe_backend(configured, monkeypatch):
    captured = {}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"id":"email_http"}'

    monkeypatch.setattr(reminders, "urlopen", lambda request, timeout: (captured.update(request=request, timeout=timeout) or Response()))
    payload = {"from": "billing@example.test", "to": ["owner@example.test"], "subject": "x", "text": "x", "html": "x"}
    assert reminders._send_resend(payload, "stable-key") == "email_http"
    assert captured["request"].get_header("Idempotency-key") == "stable-key"
    assert captured["timeout"] == 10

    contract = make_contract()
    with override_settings(MAILERS={"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}):
        assert not reminders._provider_ready()
        reminders.process_annual_term_reminders(now=contract.initial_term_end - timedelta(days=30), app="app")
    row = AnnualTermReminder.objects.get(contract=contract)
    assert row.status == "unresolved" and row.last_error_code == "resend_idempotency_transport_unavailable"


def test_scheduled_command_runs_reminder_integration_without_real_mail(configured, monkeypatch):
    from django_core_micha.billing.management.commands import billing_process_declarations as command_module
    contract = make_contract()
    calls = []
    monkeypatch.setattr(command_module, "retry_declaration_emails", lambda: None)
    monkeypatch.setattr(command_module, "process_annual_term_reminders", lambda **kwargs: (calls.append(kwargs) or (1, 0, 0)))
    monkeypatch.setattr(command_module, "resume_withdrawals", lambda: 0)
    monkeypatch.setattr(command_module, "apply_due_cancellations", lambda now: 0)
    monkeypatch.setattr(command_module, "run_transition_guard", lambda now: 0)
    output = __import__("io").StringIO()
    call_command("billing_process_declarations", stdout=output)
    assert calls and calls[0]["app"] == "app"
    assert "annual_reminders_accepted=1" in output.getvalue()


def test_command_sends_via_reminder_processor(configured, monkeypatch):
    from io import StringIO
    from django_core_micha.billing.management.commands import billing_process_declarations as command_module
    now = timezone.now()
    contract = make_contract(end=now + timedelta(days=29))
    monkeypatch.setattr(reminders, "_send_resend", lambda payload, key: "accepted")
    monkeypatch.setattr(command_module, "retry_declaration_emails", lambda: None)
    monkeypatch.setattr(command_module, "resume_withdrawals", lambda: 0)
    monkeypatch.setattr(command_module, "apply_due_cancellations", lambda now: 0)
    monkeypatch.setattr(command_module, "run_transition_guard", lambda now: 0)
    out = StringIO()
    call_command("billing_process_declarations", stdout=out)
    assert AnnualTermReminder.objects.get(contract=contract).status == "sent"
    assert "annual_reminders_accepted=1" in out.getvalue()


def test_reminder_failure_does_not_block_other_scheduled_billing(configured, monkeypatch):
    from django.core.management import CommandError
    from django_core_micha.billing.management.commands import billing_process_declarations as command_module
    monkeypatch.setattr(command_module, "retry_declaration_emails", lambda: None)
    monkeypatch.setattr(command_module, "process_annual_term_reminders", lambda **kwargs: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(command_module, "resume_withdrawals", lambda: 0)
    actions = []
    monkeypatch.setattr(command_module, "apply_due_cancellations", lambda now: actions.append("cancel") or 0)
    monkeypatch.setattr(command_module, "run_transition_guard", lambda now: actions.append("guard") or 0)
    with pytest.raises(CommandError, match="annual reminder processing failed"):
        call_command("billing_process_declarations")
    assert actions == ["cancel", "guard"]
