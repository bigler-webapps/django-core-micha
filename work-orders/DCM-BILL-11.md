# DCM-BILL-11 — Reconcile plan prices in the active Stripe account

## Part A — Envelope (everyone)

**Repository:** django-core-micha, `main`. **Tier:** 3, shared billing.
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

NOT READY FOR DISPATCH. The Orchestrator must map the current sources, add the canonical implementer preamble and progress contract, and identify the exact affected tests and billing contract before dispatch.

Target workspace: `C:/Users/Micha Bigler/Documents/webapps`; target repository: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

Context anchors: `src/django_core_micha/billing/stripe_service.py` (`sync_plans`, `stripe_fingerprint`, `_latest_price`); `billing/models.py` (`StripePrice`, unique lookup key and version); `billing/management/commands/billing_sync_plans.py`; `billing/tests/test_stripe_chunk_b.py` and the existing checkout, subscription and lifecycle tests. MAANA-UX-1 Part C records the staging observation. The Orchestrator must check the installed Stripe SDK's current error and object contracts and the consumers of sync results without reading secrets.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Authoring requested 2026-11-16. Gate #1 for implementation is pending. The register entry predates this document; it remains planned. After approval, map Part B and perform the canonical Tier-3 implementation and independent security/review checks.

### Staging acceptance after shared release

Adopt the released exact DCM pin in Maana under its own approved scope and deploy only through the authorized deployment path. Check that the normal plan synchronization reconciles stale staging mappings, with no imperative server-side data edit. If that path cannot safely repair the existing state, stop and specify a separately approved migration or management-command repair; do not silently mark the acceptance complete.

Using the established Stripe test environment and an authorized staging test account, open the annual plan's hosted checkout in German and verify the checkout locale, initial amount and the displayed continuation terms against Maana's disclosed USD 50 first year and USD 5 monthly continuation. Verify the monthly plan can open too. Record the actual provider display, any ambiguity or mismatch, and distinguish opening checkout from purchasing; do not submit a real payment. Check existing test-mode schedule evidence separately if the hosted page cannot display both phases. A successful technical check does not settle the authorized cancellation/withdrawal recipient or close MAANA-UX-1 by itself. If access or a deployment gate blocks acceptance, record the exact blocker and leave it open.

## Gate record

Gate #1 pending.
