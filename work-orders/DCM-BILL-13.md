# DCM-BILL-13 — Durable annual-term reminder delivery

**ROLE: IMPLEMENTER.** You are the already-dispatched coding instance, not the orchestrator. Implement Parts A and B yourself; do not dispatch another implementer, run reviews or perform Part C.

## Part A — Envelope (everyone)

**Repository:** django-core-micha, `main`. Consumer: Maana, MAANA-APP-41.

Tier 3 · shared billing and contractual communications.

### Outcome and scope

Provide the shared billing capability required by MAANA-APP-41 to deliver one reminder to the contract owner 30 days before an eligible initial annual term ends and continues monthly. The message uses that contract's actual end date, continuing amount and currency, monthly token allowance and cancellation path; it uses the recipient's supported language with the consumer's documented fallback. No fixed price is substituted for a missing contract value.

Use the existing shared billing and notification infrastructure. Persist per-contract delivery state and attempts. Retry definite failures through the end of the initial term; for ambiguous outcomes retry only within the provider's verified idempotency window, with the same request identity and payload. After 24 hours from the first potentially accepted request, mark it unresolved, surface it to the operator and never automatically resend it. Handle missed schedules after downtime without claiming the message was timely, and recheck eligibility before each attempt. Cancelled, withdrawn and otherwise ineligible contracts must not receive a reminder. Delivery state reports sent only after the provider accepts it. Concurrent workers, retries and restarts must not result in duplicate accepted sends. If the configured provider cannot support safe retries within its window, stop and surface the unresolved provider capability rather than claim success.

Make the capability usable by an opted-in consumer and its existing scheduled billing processing without introducing a parallel application-local notification service. Preserve existing contract, cancellation, renewal, authorization and billing behavior for existing consumers. Do not activate a production schedule, send real customer mail in tests, change secrets, or modify an application or infrastructure repository under this order. A consumer pin, rollout and any production schedule changes have their own gates.

### Required tests to write

Use synthetic contracts, controlled clocks and a fake provider. Cover the exact 30-day threshold, contract-specific terms and language fallback, concurrent workers, accepted-send idempotence, restart and retry, provider failures and ambiguous outcomes both inside and after 24 hours, retry cutoff and operator-visible unresolved state, missed schedules, and eligibility changes including cancellation and withdrawal. Verify per-account isolation, owner-only delivery, existing billing permissions and unchanged cancellation and continuation behavior. Exercise the existing scheduling integration without sending mail or dispatching production workflows.

### Risks and preconditions

The current shared notification API skips an already existing delivery even when it failed; the existing scheduled billing command does not run this reminder. Provider acceptance is not proof of inbox delivery. Exactly-once acceptance across an ambiguous provider response depends on a provider-supported idempotency or reconciliation mechanism; do not assume Django mail alone supplies one. Any decision that changes this outcome or the Maana WO is a scope change.

## Part B — Implementation map (implementer)

Workspace `C:/Users/Micha Bigler/Documents/webapps`; target repository `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

### Context package

- Own `src/django_core_micha/billing/models.py`, a new billing migration following `0004_contracts.py`, a focused reminder module, `billing/management/commands/billing_process_declarations.py`, and relevant billing tests. Reuse the shared notifications mechanisms in `src/django_core_micha/notifications/` as appropriate; changes there are within scope only when necessary for reliable delivery. No app or infrastructure changes. Report the consumer contract for the orchestrator to document in `docs/design/billing.md`.
- `Contract` holds `user`, `app`, `initial_term_end`, `continuation_amount_cents`, `continuation_token_allowance`, `continuation_interval`, `phase`, `status`, schedule and purchase references. Historical `Purchase` and `ContractPayment` hold currency; do not silently substitute a current catalogue price or currency. `billing/contracts.py` owns cancellation and continuation transitions. The existing scheduled command processes declarations, refunds and transition guards, scoped to `DCM_BILLING_APP`; Maana's `project.yaml` already schedules it daily.
- The shared email dispatcher in `notifications/dispatch.py` calls `_send_email` in `delivery.py`, which catches failures and does not return acceptance; it cannot supply the required evidence as-is. `settings/_email_config.py` selects Anymail's Resend backend for the consumer. Resend documents an HTTP `Idempotency-Key`, a 24-hour retention window, and requires identical request payloads. Installed Anymail 15.2 `backends/resend.py` places message extra headers in email JSON, not HTTP request headers: verify the actual transport boundary and ensure the key is sent as an HTTP header, never assume message headers or an arbitrary fake establish this. Preserve all existing notification users' behavior. Unknown/unsupported transports must fail closed and surface an operator-visible state; never send with unsafe retries.
- Stable identity, payload and first-attempt time must survive crashes and concurrency; never extend the 24-hour window on retry. Leave enough safety margin to ensure no request starts at or after expiry. Distinguish definite rejection from ambiguous send and be conservative where provider state cannot be proved. Prevent retries after cutoff, never change customer/subscription state, and use a configurable cancellation path and documented consumer opt-in.
- Existing coverage: `billing/tests/test_contracts_lifecycle.py`, `test_checkout_attempts.py`, `test_declarations_intake.py`, `test_operator_endpoints.py`, `test_review_fixes.py` and related security tests. Add focused synthetic reminder/provider/scheduler tests. No real provider calls in tests.

### Preamble and progress contract

Your first tool action is the hook canary: run `codex-hook-canary-ws-gov-60` exactly. The expected denial is `WS-GOV-60 canary: workspace hooks are active`; continue. If absent, report unverified coverage once and continue.

Parts A and B are your complete specification. Read nearest `AGENTS.md`, `.codex/skills/backend-engineering/SKILL.md` and app `MEMORY.md` for conventions. Stay in scope; do not modify dependencies, authorization, CI or unrelated areas, and do not edit the register or `MEMORY.md`. Your tools are for editing source and test files and running only tests you wrote, once; no installs, lockfiles, package managers, cleanup, git operations, dispatch or self-review. If blocked, report `RESULT: BLOCKED <reason>`. Leave changes uncommitted. The orchestrator owns authoritative tests and independent reviews.

Print `PLAN: <steps>` first, then `PROGRESS: [n/total] <action>` before relevant actions and completion, with no gap over about two minutes. Finish exactly once with `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — implementer: this part is not addressed to you. Do not perform reviews, update the register, commit, push or dispatch.

This is the separately gated shared-core prerequisite for MAANA-APP-41. The operator chose the 24-hour unresolved-state rule on 2026-11-17 after the Resend capability check. Core changes, migrations and release follow Tier 3; application adoption and production operations remain separate.

## Gate record

Gate #1 approved by the operator in chat on 2026-11-17, including the 24-hour unresolved-state rule.

## Completion

The shared reminder uses opt-in scheduled processing, per-contract state and immutable request payloads. Resend receives an HTTP idempotency key; ambiguous outcomes expire into an operator-visible unresolved state with no automatic resend. Eligibility and terms are rechecked before dispatch. No real mail, consumer deployment or production schedule was changed.

Independent review used `codex/gpt-6.1-terra` for the envelope and `codex/gpt-6-luna` for regression, duplication, tests and security in two rounds. Twelve deduplicated findings were accepted and fixed, including three blockers concerning crash recovery, ambiguous retries and a stale clock. An unrelated foreign scratch file mentioned in the first review was excluded from the change. Billing and notification suite: 426 passed, 3 skipped before final targeted corrections; the focused reminder suite and migration consistency check were repeated after corrections. SQLite concurrency evidence does not replace a PostgreSQL concurrency run.

Release target 3.28.0. Consumer opt-in, pin, rollout and production schedule remain outside this order.
