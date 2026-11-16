"""Durable delivery for the initial-term-to-monthly contract reminder."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from html import escape
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AnnualTermReminder, Contract

logger = logging.getLogger(__name__)
_IDEMPOTENCY_WINDOW = timedelta(hours=24)
_REQUEST_SAFETY_MARGIN = timedelta(minutes=5)
_SUPPORTED_LANGUAGES = {"de", "en", "fr"}

_TEXT = {
    "de": ("Ihre jährliche Vertragslaufzeit endet bald", "Ihre erste jährliche Vertragslaufzeit endet am {end_date}. Danach läuft Ihr Vertrag monatlich für {amount} weiter und umfasst {tokens} Tokens pro Monat. Sie können hier kündigen: {cancel_url}"),
    "en": ("Your initial annual term is ending soon", "Your initial annual term ends on {end_date}. Your contract then continues monthly at {amount} and includes {tokens} tokens per month. Cancel here: {cancel_url}"),
    "fr": ("Votre période annuelle initiale arrive à son terme", "Votre période annuelle initiale se termine le {end_date}. Votre contrat se poursuit ensuite chaque mois au tarif de {amount} et inclut {tokens} jetons par mois. Résiliation : {cancel_url}"),
}


class ProviderOutcome(Exception):
    def __init__(self, code, *, ambiguous):
        self.code = code
        self.ambiguous = ambiguous
        super().__init__(code)


def _currency(contract):
    if contract.purchase_id and contract.purchase.currency:
        return contract.purchase.currency.upper()
    payment = contract.payments.filter(billing_reason="initial").order_by("paid_at", "pk").first()
    return payment.currency.upper() if payment else ""


def _eligible(contract, now):
    return bool(
        contract.initial_term_end
        and contract.continuation_plan_key
        and contract.continuation_interval == "month"
        and contract.continuation_amount_cents is not None
        and contract.continuation_token_allowance is not None
        and contract.phase == "initial"
        and contract.status == "active"
        and contract.schedule_status == "created"
        and contract.initial_term_end > now
        and contract.started_at
        and contract.initial_term_end - contract.started_at >= timedelta(days=364)
        and contract.kind == "subscription"
    )


def _recipient_language(user):
    profile = getattr(user, "contact_profile", None) or getattr(user, "profile", None)
    language = str(getattr(profile, "language", "") or "").split("-")[0].lower()
    fallback = str(getattr(settings, "DCM_BILLING_REMINDER_LANGUAGE_FALLBACK", "en")).split("-")[0].lower()
    return language if language in _SUPPORTED_LANGUAGES else (fallback if fallback in _SUPPORTED_LANGUAGES else "en")


def _payload(contract, currency):
    cancel_url = str(getattr(settings, "DCM_BILLING_CANCELLATION_URL", "") or "")
    if not cancel_url or not currency or contract.continuation_amount_cents is None or contract.continuation_token_allowance is None:
        return None
    language = _recipient_language(contract.user)
    subject, body = _TEXT[language]
    terms = {
        "end_date": timezone.localtime(contract.initial_term_end).date().isoformat(),
        "amount": f"{contract.continuation_amount_cents / 100:.2f} {currency}",
        "tokens": str(contract.continuation_token_allowance),
        "cancel_url": cancel_url,
    }
    rendered = body.format(**terms)
    escaped = escape(rendered).replace("\n", "<br>")
    return {
        "from": str(getattr(settings, "DEFAULT_FROM_EMAIL", "")),
        "to": [contract.user.email],
        "subject": subject,
        "text": rendered,
        "html": f"<p>{escaped}</p>",
    }


def _provider_ready():
    mailers = getattr(settings, "MAILERS", {}) or {}
    backend = (mailers.get("default") or {}).get("BACKEND", "")
    anymail = getattr(settings, "ANYMAIL", {}) or {}
    return backend == "anymail.backends.resend.EmailBackend" and bool(anymail.get("RESEND_API_KEY"))


def _send_resend(payload, idempotency_key):
    """POST directly to Resend so the key is an HTTP header, not an email header."""
    api_key = settings.ANYMAIL["RESEND_API_KEY"]
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status not in (200, 201):
                raise ProviderOutcome(f"http_{response.status}", ambiguous=response.status >= 500)
            data = json.loads(response.read().decode("utf-8"))
            message_id = data.get("id") if isinstance(data, dict) else None
            if not message_id:
                raise ProviderOutcome("accepted_response_missing_id", ambiguous=True)
            return str(message_id)
    except HTTPError as exc:
        raise ProviderOutcome(f"http_{exc.code}", ambiguous=exc.code in (408, 409) or exc.code >= 500) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderOutcome(type(exc).__name__, ambiguous=True) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderOutcome("invalid_provider_response", ambiguous=True) from exc


def _ensure_reminder(contract, due_at, now):
    currency = _currency(contract)
    payload = _payload(contract, currency)
    valid = bool(payload and payload["to"][0] and payload["from"])
    reminder, _ = AnnualTermReminder.objects.get_or_create(
        contract=contract,
        defaults={
            "idempotency_key": f"dcm-annual-reminder:{contract.app}:{contract.reference}",
            "payload": payload if valid else {},
            "status": "pending" if valid else "unresolved",
            "last_error_code": "missing_reminder_data" if not valid else "",
            "due_at": due_at,
            "late": now > due_at,
        },
    )
    return reminder


def process_annual_term_reminders(*, now=None, app=None):
    """Process due reminders; return (accepted, unresolved, skipped) counts."""
    live_clock = now is None
    now = now or timezone.now()
    app = app if app is not None else getattr(settings, "DCM_BILLING_APP", "")
    if not app or not getattr(settings, "DCM_BILLING_REMINDER_ENABLED", False):
        return 0, 0, 0
    accepted = unresolved = skipped = 0
    contracts = Contract.objects.filter(
        app=app, phase="initial", status="active", kind="subscription", schedule_status="created",
        initial_term_end__isnull=False, continuation_plan_key__gt="",
        continuation_interval="month",
        initial_term_end__lte=now + timedelta(days=30), initial_term_end__gt=now,
    ).select_related("user", "purchase")
    for contract in contracts.iterator():
        if live_clock:
            now = timezone.now()
        due_at = contract.initial_term_end - timedelta(days=30)
        reminder = _ensure_reminder(contract, due_at, now)
        if reminder is None:
            skipped += 1
            continue
        if _process_one(reminder.pk, now, live_clock=live_clock):
            accepted += 1
        elif AnnualTermReminder.objects.filter(pk=reminder.pk, status="unresolved").exists():
            unresolved += 1
    expired_failed = AnnualTermReminder.objects.filter(contract__app=app, status="failed", contract__initial_term_end__lte=now)
    unresolved += expired_failed.update(status="unresolved", last_error_code="initial_term_ended")
    # Include older ambiguous attempts that aged out, even if their contract is no longer in the send window.
    expired = AnnualTermReminder.objects.filter(contract__app=app, status__in=("ambiguous", "sending"), first_attempt_at__lte=now - (_IDEMPOTENCY_WINDOW - _REQUEST_SAFETY_MARGIN))
    for reminder_id in expired.values_list("pk", flat=True).iterator():
        with transaction.atomic():
            row = AnnualTermReminder.objects.select_for_update().get(pk=reminder_id)
            if row.status in ("ambiguous", "sending"):
                row.status = "unresolved"
                row.last_error_code = "idempotency_window_expired"
                row.save(update_fields=("status", "last_error_code", "updated_at"))
                unresolved += 1
    return accepted, unresolved, skipped


def _process_one(reminder_id, now, *, live_clock=False):
    with transaction.atomic():
        row = AnnualTermReminder.objects.select_for_update(of=("self",)).select_related("contract", "contract__user", "contract__purchase").get(pk=reminder_id)
        if live_clock:
            now = timezone.now()
        if row.status == "unresolved" and row.first_attempt_at is None and row.last_error_code in (
                "missing_reminder_data", "resend_idempotency_transport_unavailable"):
            payload = _payload(row.contract, _currency(row.contract))
            if payload and payload["to"][0] and payload["from"] and _provider_ready():
                row.payload = payload
                row.status = "pending"
                row.last_error_code = ""
                row.save(update_fields=("payload", "status", "last_error_code", "updated_at"))
        if row.status in ("sent", "unresolved", "cancelled"):
            return False
        if row.status == "sending" and row.last_attempt_at and now < row.last_attempt_at + timedelta(minutes=1):
            return False
        contract = row.contract
        if not _eligible(contract, now):
            row.status = "cancelled"
            row.last_error_code = "contract_ineligible"
            row.save(update_fields=("status", "last_error_code", "updated_at"))
            return False
        if row.status in ("ambiguous", "sending") and row.first_attempt_at and now >= row.first_attempt_at + _IDEMPOTENCY_WINDOW - _REQUEST_SAFETY_MARGIN:
            row.status = "unresolved"
            row.last_error_code = "idempotency_window_expired"
            row.save(update_fields=("status", "last_error_code", "updated_at"))
            return False
        if row.status == "failed" and now >= contract.initial_term_end:
            row.status = "cancelled"
            row.last_error_code = "initial_term_ended"
            row.save(update_fields=("status", "last_error_code", "updated_at"))
            return False
        if not _provider_ready():
            row.status = "unresolved"
            row.last_error_code = "resend_idempotency_transport_unavailable"
            row.save(update_fields=("status", "last_error_code", "updated_at"))
            logger.error("Annual reminder %s unresolved: safe Resend transport unavailable", row.pk)
            return False
        previously_ambiguous = row.status in ("ambiguous", "sending")
        row.status = "sending"
        row.attempts += 1
        row.first_attempt_at = row.first_attempt_at or now
        row.last_attempt_at = now
        row.save(update_fields=("status", "attempts", "first_attempt_at", "last_attempt_at", "updated_at"))
        payload, key, attempt = row.payload, row.idempotency_key, row.attempts

    try:
        # Serialize the final eligibility check and provider request with contract updates.
        with transaction.atomic():
            contract = Contract.objects.select_for_update().get(pk=contract.pk)
            if live_clock:
                now = timezone.now()
            if now >= row.first_attempt_at + _IDEMPOTENCY_WINDOW - _REQUEST_SAFETY_MARGIN:
                AnnualTermReminder.objects.filter(pk=reminder_id, attempts=attempt).update(
                    status="unresolved", last_error_code="idempotency_window_expired")
                return False
            if not _eligible(contract, now):
                AnnualTermReminder.objects.filter(pk=reminder_id, attempts=attempt).update(
                    status="cancelled", last_error_code="contract_ineligible")
                return False
            if _payload(contract, _currency(contract)) != payload:
                AnnualTermReminder.objects.filter(pk=reminder_id, attempts=attempt).update(
                    status="unresolved", last_error_code="reminder_terms_changed")
                return False
            message_id = _send_resend(payload, key)
    except ProviderOutcome as exc:
        with transaction.atomic():
            row = AnnualTermReminder.objects.select_for_update().get(pk=reminder_id)
            if row.status != "sent" and row.attempts == attempt:
                if exc.ambiguous or previously_ambiguous:
                    row.status = "ambiguous"
                else:
                    row.status = "failed" if now < row.contract.initial_term_end else "cancelled"
                    row.first_attempt_at = None
                row.last_error_code = exc.code
                row.save(update_fields=("status", "first_attempt_at", "last_error_code", "updated_at"))
        return False
    except Exception as exc:
        with transaction.atomic():
            row = AnnualTermReminder.objects.select_for_update().get(pk=reminder_id)
            if row.status != "sent" and row.attempts == attempt:
                row.status = "ambiguous"
                row.last_error_code = type(exc).__name__
                row.save(update_fields=("status", "last_error_code", "updated_at"))
        return False
    with transaction.atomic():
        row = AnnualTermReminder.objects.select_for_update().get(pk=reminder_id)
        row.status = "sent"
        row.sent_at = now
        row.provider_message_id = message_id
        row.last_error_code = ""
        row.save(update_fields=("status", "sent_at", "provider_message_id", "last_error_code", "updated_at"))
    return True
