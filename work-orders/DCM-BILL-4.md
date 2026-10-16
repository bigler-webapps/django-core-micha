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

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and invariants. One dispatch; complete, no stubs. Money and access are involved: completing a withdrawal and the POST path must be ONE routine under the purchase row lock, and when in doubt do NOT complete (a wrongly ended access is worse than a withdrawal that waits for the next event or reconciliation).

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (docs, version and register are the Orchestrator's). Run tests with the interpreter named in your dispatch note.

### Context package (landed in 3.5.0 / 3.6.0, paths under `src/django_core_micha/billing/`)

- `withdrawal.py`: `eligibility(purchase, now)`, `refund_amount(purchase, now)` -> `RefundCalculation(time_share_cents, token_share_cents, used_cents, refund_cents)`, `_note(calc, currency)`, `_send_confirmation(purchase, now, calc, note)` (claims `withdrawal_email_sent` atomically first, so it sends at most once), `_send_after_commit(pk, now, calc, note)`, and `withdraw(purchase, now=None) -> (calc, already_withdrawn)`. `withdraw` locks the purchase (`select_for_update`), checks eligibility, locks the owner's token accounts, computes the refund, ends the access (subscription: `stripe.Subscription.cancel` with "already canceled"/404 accepted, local `Subscription` row `canceled`; team package: `TeamPackage.ends_at`), zeroes the purchase's remaining tokens with `expiry` rows referenced `withdrawal:<purchase id>:<entry id>`, calls `stripe.Refund.create(payment_intent=, amount=, metadata={"app", "purchase_id"}, reason="requested_by_customer", idempotency_key=f"dcm-withdrawal:{app}:{purchase.pk}")`, marks the purchase `withdrawn` (`withdrawn_at`, `refund_cents`, `stripe_refund_id`, `withdrawal_note`) and sends the e-mail through `transaction.on_commit`. `_owner_accounts(purchase)` limits token entries to the purchase owner's account.
- `stripe_service.py`: `process_stripe_event(event)` (caller owns the transaction; the webhook view wraps it in `transaction.atomic()` with the `StripeEvent` row lock, status `processed|ignored|failed`; an exception rolls back and answers 500 so Stripe retries). `charge.refunded` is currently answered `"processed"` as a recording no-op BEFORE the app/metadata filter. `_dict(obj)` normalises Stripe objects, `_stripe()` is the lazy SDK accessor, `_event_app_and_metadata(kind, obj)`. `_upsert_subscription` already refuses to reopen a withdrawn purchase (row-locked read).
- `management/commands/billing_reconcile.py`: lists subscriptions and then `stripe.Event.list(created gte since)`, runs every event through `process_stripe_event` inside `transaction.atomic()` and records `StripeEvent` rows; failures become `CommandError` with the class name only.
- Tests to model on: `tests/test_withdrawal_execution.py` (fixtures `user(i)`, `purchase(...)`, `stripe_mock(...)` with `calls["cancel"]` / `calls["refund"]`, `django_capture_on_commit_callbacks`), `tests/test_review_round_two.py`, `tests/conftest.py` (factory requests skip CSRF).

### Fixed design

1. **One completion routine** in `withdrawal.py`: `complete_withdrawal(purchase_pk, *, now, refund_cents, refund_id, calc=None, note=None, event=False)`. It must be called inside a transaction, locks the purchase row itself (`select_for_update`), and returns `(result, already_withdrawn)`. If the purchase is already `withdrawn` it changes nothing and reports `already_withdrawn=True`. Otherwise it performs, in this order, exactly what `withdraw` does after the refund today: end the access (subscription cancel at Stripe with the same tolerant handling and local row, team package `ends_at`), zero the purchase's remaining tokens with the same idempotent `withdrawal:<purchase id>:<entry id>` rows (owner's accounts only, accounts locked first), mark the purchase `withdrawn` with `withdrawn_at=now`, `refund_cents`, `stripe_refund_id`, `withdrawal_note`, and register the confirmation e-mail through `transaction.on_commit`. `withdraw()` is refactored to: lock, check eligibility, compute, call `stripe.Refund.create` (unchanged arguments, plus metadata key `dcm_withdrawal` = the idempotency key string, see 2), then call `complete_withdrawal` in the same transaction. No behaviour of the POST path changes (existing withdrawal tests stay green); the end-access code exists once, in the routine.
2. **Recognising a withdrawal refund**: `withdrawal_key(app, purchase_pk)` returns `f"dcm-withdrawal:{app}:{purchase_pk}"` and is used both for the idempotency key and for the new refund metadata `dcm_withdrawal`. A Stripe refund object belongs to a withdrawal ONLY when its `metadata.dcm_withdrawal` equals the key of a purchase of this app, or — for refunds created by 3.5.0/3.6.0 before this key existed — when `metadata.app == DCM_BILLING_APP` and `metadata.purchase_id` is a purchase id and `reason == "requested_by_customer"`. Nothing else qualifies: not the amount, not the payment intent alone, not a refund made in the dashboard. Further checks before completing, each a refusal that leaves everything untouched: the refund `status == "succeeded"`; the purchase exists, belongs to this app (the app is already known from the metadata) and is `active`; `refund.payment_intent` equals the purchase's `stripe_payment_intent_id`; `0 < refund.amount <= purchase.amount_paid_cents`. `purchase_id` is untrusted text from the payload: parse as int defensively, never format it into a query unsanitised.
3. **Event handling** (`stripe_service.py`): add a function `complete_withdrawal_from_refund(refund)` that applies design 2 and calls `complete_withdrawal(..., refund_cents=refund.amount, refund_id=refund.id, now=timezone.now() or the refund `created` time converted to UTC, note=<a short text like the POST note but built from the refund amount: "Completed from the Stripe refund event; refund: <n> <currency> cents.">)`. Wire it in `process_stripe_event`: `refund.created` and `refund.updated` (the event object is the refund) are processed BEFORE the app/metadata filter (refunds carry no plan metadata); `charge.refunded` (object is a charge): to avoid an API call for every refund of the estate, first look for an ACTIVE purchase with `stripe_payment_intent_id == charge.payment_intent`; if there is none answer `"processed"` as today without calling Stripe; if there is one, list the charge's refunds with `_stripe().Refund.list(charge=<charge id>, limit=100)` and apply `complete_withdrawal_from_refund` to each (an exception from the API call propagates, so the webhook answers 500 and Stripe retries). Every other path of these events keeps its current result (`"processed"` for a recognised and completed refund, `"processed"` for a refund that is not a withdrawal — manual refunds and disputes behave as today, nothing is changed for them).
4. **Reconciliation**: `billing_reconcile` gets a refund pass after the event pass: list `stripe.Refund.list(created={"gte": since})` (auto-paging like the existing loops), keep only objects that pass design 2, and complete each inside its own `transaction.atomic()`; the count it prints includes completed withdrawals. Errors surface as today (class name only). The existing event pass already feeds the new event types through `process_stripe_event`, so both routes use the same routine.
5. **Locking and ordering**: the routine locks the purchase row first and re-reads status under the lock, so (a) webhook first then POST: the POST finds `withdrawn` and returns the stored result with `already_withdrawn: true` and does not call Stripe again; (b) POST first then webhook: the webhook finds `withdrawn` and does nothing (no second e-mail, no second ledger rows); (c) concurrent: the second waits on the row lock and then sees `withdrawn`. E-mail: still at most once — the `withdrawal_email_sent` claim is unchanged and shared.
6. **Settings/docs**: no new setting. The Stripe webhook endpoint of each app must subscribe `refund.created` and `refund.updated` (and may keep `charge.refunded`); the Orchestrator documents this.

### Tests (new file `tests/test_withdrawal_refund_event.py`; update the existing `charge.refunded` no-op assertions only where the new behaviour requires it)

Crash after the refund then the webhook completes the withdrawal (simulate: run the Stripe part, roll the local transaction back or build a purchase whose refund exists at Stripe and whose row is still active; assert access ended, subscription cancelled and renewal stopped, ledger `expiry` rows, status/refund id/amount, exactly one e-mail); webhook first then the POST returns the stored result with `already_withdrawn` and `stripe.Refund.create` is not called again; POST first then the webhook changes nothing (one e-mail, no extra ledger rows); concurrent execution (two threads or two connections with `transaction=True`, completing once; skip with a clear reason only if the test database cannot do real concurrency); a manual refund without the key and with a foreign `app` or a purchase id of another app/user is ignored (purchase stays active); a refund with a wrong payment intent, an amount above the paid amount, or status other than `succeeded` is refused; the legacy metadata form (`app` + `purchase_id`) completes; `charge.refunded` with no active purchase for its payment intent makes no API call; with one it lists the refunds and completes; `billing_reconcile` completes a pending withdrawal from `Refund.list`; a replayed event (same `event_id`) is idempotent; the existing POST tests keep passing.

### Invariants

One routine for end-of-access under the purchase row lock; recognition only through our key/metadata; a manual refund or dispute never ends access; no secret or payload in logs or responses; integer cents; e-mail at most once; no behaviour change for the POST path.

### Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator 2026-10-17 (in the Maana Expertenchat, session local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Start after DCM-RCPT-1. Seams: the DCM-BILL-3 withdrawal service and its row lock, the billing webhook
handler and event dispatch, the reconciliation command, the confirmation e-mail. Tier 3: all lenses
plus `sec_reviewer`. Release and consumer pin bumps per the dcm publish flow.
