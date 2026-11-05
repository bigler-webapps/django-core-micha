# DCM-BILL-9 — Billing follow-ups from the DCM-BILL-8 closing review

## Part A — Envelope (everyone)

**Repository:** `django-core-micha`, trunk `main`; patch release after 3.21.0.
**Tier:** 3 — refunds, subscription state and a public intake endpoint inside shared core.
**Ordering:** must be released and pinned by maana (MAANA-APP-43) before maana's develop→main promotion activates paid
annual checkout.

### Goal and expected outcome

Fix what the DCM-BILL-8 closing review carried below blocker. Expected outcome: refunds and cancellations stay correct
when Stripe events arrive late, fail or come from outside the app; the public declaration intake gives no timing
signal about whether a submission matched a contract; the listed test gaps are closed. No behaviour change for apps
that have not opted into `contract:`.

### Settled decisions (operator go 2026-11-05; details set by the Expertenchat)

1. **Failed refunds:** the `refund.failed` webhook and the fresh-key retry use one error code, so a provider-failed refund
   always gets a fresh row and idempotency key on retry.
2. **Scheduled cancellation:** when the batch job ends a scheduled cancellation before Stripe's deletion webhook arrives,
   the local subscription ends at the same time. The later webhook is idempotent.
3. **Provider-originated refunds:** partial refunds made in Stripe without `dcm_refund` metadata are reconciled into
   `refunded_cents` and the refund preview.
4. **Withdrawal resumption:** resuming a withdrawal uses the fresh-key retry and never replays a failed refund row.
5. **Public intake timing:** matched and unmatched public submissions do the same work before the response; every step
   that depends on a match runs after the response.

### Non-goals

New features, configuration keys or API fields; changes to apps; the confirmation-link format (each app's own setting).

### Risks

A refund issued twice, or a refund lost, is real money; tests must cover replay and ordering. A changed error code must
not orphan existing rows on staging.

### Required tests to write

One test per decision 1–5 (including event order: webhook before and after the batch job; replayed webhook), plus the
carried gaps: endpoint-level background mail, `me/` plan name, opt-out 404 on every new endpoint, command-level retry,
resolver with a future date, transition guard scoped to the app.

## Part B — Implementation map (implementer)

Implemented by the Orchestrator as author (operator decision of 2026-11-02, no Luna dispatch); no implementer preamble applies. Mapped 2026-11-05.

Seams (all in `src/django_core_micha/billing/`):
- Decision 1: `stripe_service.complete_contract_refund` stored `event_kind` as `last_error_code`; `contracts.retry_refund` matches `refund_failed`. The webhook now writes `refund_failed`; `contracts.REFUND_FAILED_CODES` also accepts the legacy `refund.failed` so rows from 3.21.0 are retried. A `cancelled` row ignores later events (a replayed failed event revived the replaced row).
- Decision 2: `contracts.apply_due_cancellations`, branch `period_end <= now` with a local subscription still `active`: set `status=canceled`, `ended_at`, `cancel_at_period_end=False`. `stripe_service._upsert_subscription` used `Declaration` without importing it (NameError on the deletion webhook of a scheduled cancellation).
- Decision 3: new `stripe_service.reconcile_provider_refunds`, called at the end of the `charge.refunded` branch of `process_stripe_event` after our own refund rows were completed. Provider total (`amount_refunded`) minus our open rows, capped at the payment, only ever raising `refunded_cents`.
- Decision 4: `contracts._perform_withdrawal` and `_allocate_refund` call `retry_refund` (failed row to fresh row and key) instead of `process_refund`.
- Decision 5: `declarations.create_public_declaration` stores the row and defers `finish_public_declaration` (match, link, token, confirmation mail, acknowledgement) via `_in_background` on commit; `retry_declaration_emails` finishes a declaration still `received` after five minutes.
- Release: `pyproject.toml` 3.21.1, `CHANGELOG.md`. No migration.
- Tests: new `billing/tests/test_bill9_followups.py` (self-contained fixtures; the suite runs with the `billing` extra, a venv with `.[test,billing,scripts]`). Do-not-touch: models, migrations, the contract policy, the confirmation-link format.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator on 2026-11-05 in the Maana Expertenchat (session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce): "Können wir die vorgeschlagenen WO bauen". The findings come from the
DCM-BILL-8 register row (carried). Tier 3: all lenses, `sec_reviewer`. Release as a patch, verify publication before maana
pins it.
