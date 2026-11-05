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

To be filled by the Orchestrator.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator on 2026-11-05 in the Maana Expertenchat (session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce): "Können wir die vorgeschlagenen WO bauen". The findings come from the
DCM-BILL-8 register row (carried). Tier 3: all lenses, `sec_reviewer`. Release as a patch, verify publication before maana
pins it.
