# DCM-BILL-4 — Complete a withdrawal from Stripe's refund event

## Part A — Envelope (everyone)

**Tier:** 3 — access and money state after a refund. **Repository:**
`bigler-webapps/django-core-micha`. **Precondition:** DCM-BILL-3 has landed (released in 3.5.0).
**Consumers:** every app with billing (Maana first).

### Goal and expected outcome

The security round on the released DCM-BILL-3 diff (3.5.0) found a residual case: if the server
crashes after Stripe has accepted the withdrawal refund but before the local transaction commits, the
purchase stays active locally. Money is neither lost nor paid twice (the refund carries the
idempotency key `dcm-withdrawal:<app>:<purchase id>`, a retry reuses it), but until the person
withdraws again they keep access although they were refunded — and they have no reason to try again,
because the money arrived.

Expected outcome: the withdrawal completes on our side without any action by the person as soon as
Stripe reports the refund.

### Settled decisions (operator, 2026-10-16)

1. **Trigger:** the billing webhook handler recognises a refund event (`charge.refunded` or
   `refund.created`/`refund.updated` with status succeeded, whichever DCM-BILL-1 already subscribes)
   that belongs to a withdrawal — identified by the refund's idempotency key or metadata
   `dcm-withdrawal:<app>:<purchase id>` — for a purchase that is not yet marked withdrawn.
2. **Completion:** the handler performs exactly what the confirmed withdrawal path does after the
   refund: marks the purchase withdrawn with the refund's amount and time, ends the access granted by
   that purchase, stops renewal, writes the ledger entries, and sends the confirmation e-mail — at
   most once across both paths.
3. **Idempotency across paths:** the POST path and the webhook path share one completion routine
   under the purchase row lock; whichever runs second finds the purchase withdrawn and does nothing
   (the POST returns the stored result with `already_withdrawn: true`).
4. **Refunds that are not withdrawals** (manual refunds by the operator in Stripe, disputes) are not
   treated as withdrawals by this order; they keep today's handling.
5. **Reconciliation:** the existing reconciliation from the Stripe API completes such a pending
   withdrawal too, so a missed webhook is also repaired.
6. **Visibility:** the completion is recorded like a normal withdrawal, so apps and the
   bigler-consult backoffice see one withdrawal, not a refund without withdrawal.

### Scope and acceptance

Webhook recognition, shared completion routine, reconciliation path, tests; no API or UI change.

### Non-goals and do-not-touch

Eligibility and refund calculation (DCM-BILL-3), manual refunds and disputes, the UI.

### Risks

Treating a manual refund as a withdrawal would end access wrongly; recognition must rely only on the
withdrawal key/metadata. Two paths completing concurrently could send two e-mails or double ledger
entries; the shared routine under the row lock prevents it.

### Required tests to write or update

Crash after refund then webhook completes the withdrawal (access ended, renewal stopped, ledger,
one e-mail); webhook first then POST returns `already_withdrawn`; POST first then webhook does
nothing; concurrent webhook and POST complete once; manual refund without withdrawal key is ignored
by this path; reconciliation completes a pending withdrawal; replayed webhook is idempotent.

## Part B — Implementation map (implementer; Orchestrator completes before dispatch)

Placeholder — dispatch-blocking. The Orchestrator fills the context package, target directory,
progress contract and implementer preamble before any dispatch.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 pending. Seams: the DCM-BILL-3 withdrawal service and its row lock, the billing webhook
handler and event dispatch, the reconciliation command, the confirmation e-mail. Tier 3: all lenses
plus `sec_reviewer`. Release and consumer pin bumps per the dcm publish flow.
