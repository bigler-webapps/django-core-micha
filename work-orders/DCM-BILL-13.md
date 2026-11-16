# DCM-BILL-13 — Durable annual-term reminder delivery

## Part A — Envelope (everyone)

**Repository:** django-core-micha, `main`. Consumer: Maana, MAANA-APP-41.

Tier 3 · shared billing and contractual communications.

### Outcome and scope

Provide the shared billing capability required by MAANA-APP-41 to deliver one reminder to the contract owner 30 days before an eligible initial annual term ends and continues monthly. The message uses that contract's actual end date, continuing amount and currency, monthly token allowance and cancellation path; it uses the recipient's supported language with the consumer's documented fallback. No fixed price is substituted for a missing contract value.

Use the existing shared billing and notification infrastructure. Persist per-contract delivery state and attempts. Retry failures through the end of the initial term, surface unresolved failures to the operator, handle missed schedules after downtime without claiming the message was timely, and recheck eligibility before each attempt. Cancelled, withdrawn and otherwise ineligible contracts must not receive a reminder. Delivery state reports sent only after the provider accepts it. Concurrent workers, retries and restarts must not result in duplicate accepted sends. If the configured provider cannot guarantee that outcome after an ambiguous response, stop and surface the unresolved provider capability rather than claim success or silently weaken the requirement.

Make the capability usable by an opted-in consumer and its existing scheduled billing processing without introducing a parallel application-local notification service. Preserve existing contract, cancellation, renewal, authorization and billing behavior for existing consumers. Do not activate a production schedule, send real customer mail in tests, change secrets, or modify an application or infrastructure repository under this order. A consumer pin, rollout and any production schedule changes have their own gates.

### Required tests to write

Use synthetic contracts, controlled clocks and a fake provider. Cover the exact 30-day threshold, contract-specific terms and language fallback, concurrent workers, accepted-send idempotence, restart and retry, provider failures and ambiguous outcomes, retry cutoff and operator-visible unresolved state, missed schedules, and eligibility changes including cancellation and withdrawal. Verify per-account isolation, owner-only delivery, existing billing permissions and unchanged cancellation and continuation behavior. Exercise the existing scheduling integration without sending mail or dispatching production workflows.

### Risks and preconditions

The current shared notification API skips an already existing delivery even when it failed; the existing scheduled billing command does not run this reminder. Provider acceptance is not proof of inbox delivery. Exactly-once acceptance across an ambiguous provider response depends on a provider-supported idempotency or reconciliation mechanism; do not assume Django mail alone supplies one. Any decision that changes this outcome or the Maana WO is a scope change.

## Part B — Implementation map (implementer)

NOT READY FOR DISPATCH. After Gate #1 the Orchestrator maps the shared billing contract, delivery/provider guarantees, existing scheduler and tests, and adds the implementer preamble and progress contract. Workspace `C:/Users/Micha Bigler/Documents/webapps`; target repository `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

## Part C — Orchestrator only

STOP — this part is not addressed to the implementer.

This is the separately gated shared-core prerequisite explicitly required by MAANA-APP-41. Review provider idempotency and ambiguity against the actual configured transport before mapping; if the required guarantee is unavailable, return to the operator rather than relaxing the Envelope. Core changes, migrations and release follow Tier 3; application adoption and production operations remain separate.

## Gate record

Gate #1 pending.
