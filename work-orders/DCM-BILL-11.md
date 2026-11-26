# DCM-BILL-11 — Reconcile plan prices in the active Stripe account

**ROLE: IMPLEMENTER.** You are the already-dispatched coding instance, not the orchestrator. Implement Parts A and B yourself. Do not invoke orchestration, orient, dispatch, reviewers, or another implementer. Part C is not addressed to you.

## Part A — Envelope (everyone)

**Repository:** django-core-micha, `main`.
**Tier 3:** shared billing.
**Consumer:** Maana, MAANA-UX-1. No schema change.

### Outcome

Plan synchronization verifies that the price used by a consumer is usable in the Stripe account currently configured for that consumer. An unchanged local fingerprint alone must not be reported as a successful synchronization when the stored remote price is missing. Synchronization repairs the affected current mapping safely and reports the outcome. Repeated runs converge without creating duplicate products or prices.

On 2026-11-14, Maana staging had six of seven stored price IDs absent from the account used by its current test key, although matching versioned lookup keys existed there. Annual checkout returned 502 because Stripe returned `resource_missing`. A repeated sync skipped the rows because fingerprints matched. This is historical evidence, not a claim about today's live account.

### Scope and acceptance

- For each configured plan, verify the stored current remote price against the active account before marking it unchanged. Verify that an existing active remote price and its product correspond to the intended app, plan, lookup key, currency, amount, recurrence and tax configuration before adopting them. An incompatible or ambiguous object is an explicit error; never silently adopt a wrong price.
- When the stored price is definitively missing, reconcile against the existing versioned lookup key in the active account. Reuse a verified matching price, or create the missing product and price with the current plan's semantics if no matching price exists. Repair the current mapping coherently, preserving historical versions and existing subscriptions. A second sync must be idempotent.
- Distinguish a confirmed missing resource from authentication, permission, timeout, rate-limit and transient provider errors. Such failures must not trigger a replacement, local rewrite or a misleading successful result. Handle inactive/deleted price or product as an explicit non-checkoutable condition, never as an unchanged success.
- Preserve existing behavior for genuinely changed plans, existing checkout attempts and subscription schedules, application isolation, tax and currency, and dry-run: dry-run makes no Stripe or database writes. Do not expose credentials or alter customer/subscription objects.
- Report unchanged, repaired, created and failed cases clearly through the existing management command. An operation which cannot establish a safe mapping must fail rather than leave an apparently valid checkout reference.

### Required tests to write

Use fake Stripe accounts and synthetic plans to cover: same fingerprint and valid current price; stale account IDs with matching lookup key; missing price and missing product; safe creation and repeat sync without duplicates; mismatched amount, currency, recurring interval, tax, app/plan metadata or inactive objects; changed fingerprint and versioning; two apps with similar names; dry-run without writes; provider authentication, rate-limit and transient failures without replacement or local mutation. Exercise the management command result and checkout's use of the repaired price, retaining billing authorization, idempotency, existing subscription and schedule coverage. Include a race/retry case ensuring a failed or repeated sync cannot bind a mismatched remote object.

### Preconditions, risks and non-goals

Stripe prices are immutable, and lookup keys may exist on a different or incompatible product. A lookup key alone is not sufficient proof of equivalence. Rebinding the current mapping must not modify historical subscriptions or prices. No production Stripe operation, data deletion, credential changes, migration, live purchase, pricing-policy change or weakening of billing permissions is authorized. Provider and database failures must remain visible and recoverable.

The shared release and Maana's exact pin are prerequisites for the staging acceptance below. Staging deployment and any mutating workflow dispatch retain the applicable separate operator gate. The authorized recipient for cancellations and withdrawals is a separate MAANA-UX-1 dependency and is not decided here.

## Part B — Implementation map (implementer)

Target workspace: `C:/Users/Micha Bigler/Documents/webapps`; target repository: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

### Context package

- Change `src/django_core_micha/billing/stripe_service.py` (`sync_plans`, `stripe_fingerprint`, `_latest_price`), `billing/management/commands/billing_sync_plans.py` as needed, and billing tests including `billing/tests/test_stripe_chunk_b.py` and a focused reconciliation test module. Work from these seams; inspect named files and relevant billing tests as needed.
- `StripePrice` in `billing/models.py` has a globally unique lookup key, app, plan key, version, fingerprint, product ID and price ID. `_latest_price` selects the greatest version for the configured app and plan; checkout consumes that row's price ID. Preserve old rows and subscriptions; no migration.
- The existing sync skips matching fingerprints, otherwise locates a product by exact app/plan metadata, versions the lookup key as `app:plan:vN`, creates an exclusive-tax price, and returns `(plan_key, result)` pairs. The command prints these and converts exceptions to a sanitized `CommandError`. Existing fake Stripe objects in `test_stripe_chunk_b.py` will need the full fields and retrieve/error behavior needed by the new verification. Preserve caller compatibility where possible.
- Stripe is an optional billing dependency (`stripe>=16.0.1`); the host has 16.0.0, so use the installed SDK's error and object interfaces without changing dependencies. Do not inspect secrets or contact live Stripe. Review the SDK classes locally where necessary. Existing checkout, subscription, authorization and lifecycle tests are in `src/django_core_micha/billing/tests/`.

### Preamble and progress contract

Your first tool action is the hook canary: run the shell command `codex-hook-canary-ws-gov-60` exactly. The expected result is a denial, `WS-GOV-60 canary: workspace hooks are active`; continue after it. If it is not denied, report unverified hook coverage once and continue.

Part A and this map are the complete specification. Read the nearest `AGENTS.md`, `.codex/skills/backend-engineering/SKILL.md`, and app `MEMORY.md` only for conventions. Stay in scope; do not touch auth, permissions, dependencies, schema or CI, and do not update `MEMORY.md` or the register. Your tools are for editing source and test files and running only the tests you wrote, once; no installs, lockfiles, package managers, cleanup, git operations or self-review. If blocked, report `RESULT: BLOCKED <reason>`. Leave changes uncommitted. The orchestrator runs the authoritative tests and independent reviews.

Narrate continuously: `PLAN: <steps>` first, then `PROGRESS: [n/total] <action>` before each relevant action and on completion, with gaps under about two minutes; finish with exactly one `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — implementer: do not perform anything below, launch another implementer, review, update the register, commit or push.

Authoring requested 2026-11-16. The register entry predates this document. Perform canonical Tier-3 implementation and independent security/review checks.

### Staging acceptance after shared release

Adopt the released exact DCM pin in Maana under its own approved scope and deploy only through the authorized deployment path. Check that the normal plan synchronization reconciles stale staging mappings, with no imperative server-side data edit. If that path cannot safely repair the existing state, stop and specify a separately approved migration or management-command repair; do not silently mark the acceptance complete.

Using the established Stripe test environment and an authorized staging test account, open the annual plan's hosted checkout in German and verify the checkout locale, initial amount and the displayed continuation terms against Maana's disclosed USD 50 first year and USD 5 monthly continuation. Verify the monthly plan can open too. Record the actual provider display, any ambiguity or mismatch, and distinguish opening checkout from purchasing; do not submit a real payment. Check existing test-mode schedule evidence separately if the hosted page cannot display both phases. A successful technical check does not settle the authorized cancellation/withdrawal recipient or close MAANA-UX-1 by itself. If access or a deployment gate blocks acceptance, record the exact blocker and leave it open.

## Gate record

Gate #1 approved by the operator in chat on 2026-11-16 for DCM-BILL-11.

## Implementation and outstanding acceptance

The implementation verifies Stripe price and product semantics, repairs confirmed missing current prices and preserves historic versions. Independent review: envelope `codex/gpt-6.1-terra`, regression, duplication, tests and security `codex/gpt-6-luna`; five findings in round one and three in round two accepted and fixed. Billing tests: 225 passed, one skipped before review fixes; 41 directly affected tests passed after fixes. Release target: 3.26.2.

Staging acceptance remains open: Maana's exact pin and staging deployment require their own approved scope and deployment gate. Hosted checkout and test-mode schedule evidence have not been checked. No live Stripe operation was performed.

2026-11-22 evidence reconciliation: Maana subsequently deployed DCM 3.37.0 and a CHF catalogue. MAANA-APP-42 records successful staging sandbox checkout openings for monthly and annual plans on 2026-11-19; MAANA-UX-1 records the German annual checkout and both phases disclosed before checkout. The operator accepted that Stripe's hosted page displays only the first phase. The CHF deploy created new price versions in the current account, so those results do not establish that the original stale current mappings were repaired by the unchanged-fingerprint path. The original USD amounts above are historical; the remaining reconciliation and schedule evidence must be assessed against the current approved CHF contract. Keep this order open until those criteria are demonstrated or explicitly excepted. Part B: accurate.
