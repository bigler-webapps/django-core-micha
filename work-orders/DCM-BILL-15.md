# DCM-BILL-15 — Subscriptions for app objects

## A. Envelope

**Tier:** 3 (shared billing, access and schema). **Target:** `django-core-micha`, `main`. **First consumer:** Cinevia (`CIN-BILL-2`).

### Goal and expected outcome

An app can sell a recurring subscription for one of its objects. In Cinevia, one person pays for a film project and the subscription determines that project's member limit. The app can read the project's paid or granted access and apply its own membership rules.

### Existing capabilities

DCM already provides a per-app `billing.yaml` catalogue, versioned Stripe price sync, Checkout, Customer Portal, verified webhooks, reconciliation, operator grants and contract declarations. Maana uses the existing personal and team billing flows with a recurring token allowance or shared token pool. UCM's subscription and withdrawal views are account-oriented. The workspace catalogue records both cores in `webapp-management/SHARED_CAPABILITIES.md`.

### Scope and acceptance

- **Catalogue.** Declare a distinct object subscription plan kind in `billing.yaml` with a monthly or annual interval, a positive member limit and an amount in the app's configured currency. Monthly and yearly variants have separate plan keys. Prices are final amounts including applicable VAT. Validate the new kind and sync it using the existing Stripe Managed Payments integration. For Cinevia, verify that Swiss CHF checkout and recurring invoices match the advertised gross amounts. When a tariff change creates a prorated charge, verify its calculation separately.
- **Object and permissions.** Identify an object by the app key and a stable opaque object key. The app establishes that the object exists and authorizes purchases and state reads before exposing those operations to its users. Cinevia allows project admins to start a purchase. DCM binds portal access and contract actions to the payer of the specific purchase. Another project admin cannot operate that payer's billing. The payer can manage the running subscription if they leave the project. After it ends, an eligible project admin can buy again.
- **Checkout.** Each object can have at most one active access subscription and one in-flight purchase attempt for that access, with a new purchase refused while either blocks it. Parallel requests from different eligible buyers, retries and delayed payments must converge without a duplicate Stripe subscription or charge. An expired or definitively failed attempt releases the object for another purchase. A return from Checkout alone cannot grant access. A verified Stripe event or reconciliation confirms the payment.
- **Plan changes and cancellation.** The payer uses the Stripe portal for the target subscription and its app's eligible object plans. Upgrades take effect after confirmation of any amount due. Downgrades take effect at the end of the paid period. These rules also cover switches between monthly and yearly plans. Ordinary cancellation ends renewal at the end of the paid month or paid year. Access remains through the paid term. Provider events and reconciliation agree on the effective plan and member limit, including scheduled changes, failed payments, duplicate delivery and out-of-order delivery.
- **Object state and grants.** Provide an app-facing state query with the source (subscription, operator grant or none), plan key and name, member limit, billing status, paid-through date, renewal or cancellation status and payer when available to the app. Authorized operators can grant and revoke unlimited object access with a required reason and an audit record that identifies the operator. A grant controls effective access independently of the paid contract and preserves the paid subscription's details. The operator overview reports object subscriptions separately from personal account counts.
- **Contracts and tokens.** Keep the purchaser as the contract holder and apply the app's existing configured contract, ordinary cancellation, withdrawal and refund policies to the object purchase. A valid withdrawal ends the paid access tied to that contract and refunds its attributable payments according to the configured policy. Events and reconciliation must not restore withdrawn access. Keep token purchases and renewed token allowances independent of project access purchases, including when the same Stripe customer owns both. A cancellation, withdrawal or refund affects only the associated purchase, payment and entitlement. In particular, the one-checkout rule must permit a separate token purchase and must not block renewal of an existing subscription with token allowances. Conversely, ending an unrelated token-bearing contract must leave the project subscription in place.
- **Compatibility and delivery.** Preserve existing personal subscriptions, team packages, team seats, tokens, grants, withdrawal and contract behaviour and existing API response keys. Object subscriptions grant no personal access and have no implicit token allowance. Deliver additive migrations, consumer-facing billing contract documentation, a changelog entry and a minor DCM release suitable for pinning by Cinevia.

### Exclusions

Cinevia owns project membership, invitation limits, free-tier eligibility and read-only enforcement. The first consumer work order owns app integration, project settings, UI, classification of existing projects and go-live. This shared-core order covers compatibility with existing token offerings. It does not add a Cinevia token product, a new token plan kind, a payer-transfer flow, a company licence, or changes to another app's plans or CHF prices.

### Risks

- An incorrect subject or plan association can grant access to a different project or to the payer personally.
- A local uniqueness check after payment cannot prevent a second charge. Provider delays and concurrent buyers must be covered at checkout creation and through payment confirmation.
- Using the whole Stripe customer as the billing scope can affect other subscriptions or token credits. Portal, contract actions, refunds and reconciliation need purchase-level scope.
- The existing Stripe price sync verifies exclusive tax behaviour. Gross pricing for the new plans and the required portal transitions depend on actual Managed Payments behaviour. Sandbox evidence is required for these paths.

### Required tests to write

- Catalogue and sync: reject invalid intervals and non-positive member limits, check gross-price semantics for object plans, preserve existing price semantics and verify the active Stripe price mapping.
- Checkout and permissions: reject unknown objects at the app authorization boundary, reject a non-authorized buyer, isolate identical object keys across apps and reject access to another payer's portal or contract. Exercise concurrent starts by two eligible buyers, expiry, delayed completion and retry after definitive failure. Confirm at most one Stripe purchase and no entitlement on browser return alone.
- Lifecycle: confirm purchase, renewal, paid upgrade, downgrade at period end, month/year changes, cancellation at monthly and annual expiry, and failed payment. Exercise duplicate and reordered Stripe events and reconciliation. Verify the effective member limit and that a stale event cannot restore a withdrawn subscription.
- State and operations: verify paid, free, ended and absent states, grant and revoke with a mandatory reason and operator audit, ongoing billing state under a grant, separate operator counts and unchanged personal access for the payer and project members.
- Contracts and tokens: use one synthetic payer for an object subscription, a separate token purchase and an existing subscription with a recurring token allowance. Verify independent checkout and renewal, correct attribution of contract payments and refunds, and preservation of the unrelated balance and contract when project access is withdrawn. Verify the inverse when an unrelated token-bearing contract ends. Cover the affected existing personal, team, token, contract and permission regressions.

## B. Implementation map

- `src/django_core_micha/billing/config.py` owns plan kinds, currency, catalogue validation and contract settings.
- `src/django_core_micha/billing/models.py` and `src/django_core_micha/billing/migrations/` own subscriptions, checkout attempts, purchases, grants, contracts and token-account persistence.
- `src/django_core_micha/billing/views.py` and `src/django_core_micha/billing/urls.py` own the checkout, attempt and portal HTTP contracts.
- `src/django_core_micha/billing/services.py` owns personal access and token services. `src/django_core_micha/billing/operator.py` and `src/django_core_micha/billing/audit_config.py` cover grants, aggregates and audit.
- `src/django_core_micha/billing/stripe_service.py` owns price and customer mapping, Checkout parameters, Stripe event handling and subscription updates.
- `src/django_core_micha/billing/management/commands/billing_sync_plans.py` and `src/django_core_micha/billing/management/commands/billing_reconcile.py` are the existing operator entry points.
- `src/django_core_micha/billing/contracts.py`, `src/django_core_micha/billing/declarations.py` and `src/django_core_micha/billing/withdrawal.py` own declaration and refund lifecycle.
- `src/django_core_micha/billing/tests/` contains provider fixtures and checkout, reconciliation, contracts, withdrawal, access and operator tests.
- `docs/design/billing.md`, `CHANGELOG.md` and `pyproject.toml` cover the public contract and package release.
- Environment: the existing Stripe Managed Payments integration and synthetic test data. Real Stripe evidence uses Sandbox. Concurrency evidence needs a database with production-equivalent locking semantics.

## Preamble

> **You are the dispatched implementer of this work order, not its Orchestrator. Rules in `AGENTS.md`
> and in skills that address the Orchestrator, a Codex parent or the Expertenchat (delegation, dispatch,
> review, register, landing) do not apply to you. You never run `codex` or `codex exec`, never write to
> git, and never edit the register or `.claude/codex-status.md`.**
>
> **Your first tool action is the hook canary: run the shell command `codex-hook-canary-ws-gov-60`
> exactly (it is a command string, not a file). The expected result is a denial,
> `WS-GOV-60 canary: workspace hooks are active`. Continue after it. If it is not denied, report
> that once as unverified hook coverage and continue.**
>
> The text above is the complete specification. Read the nearest `AGENTS.md`, relevant role skill and
> repo `MEMORY.md` for conventions. Edit the scoped source and tests and run only the new tests you
> wrote. The Orchestrator runs the authoritative affected set and independent reviews. Do not install
> dependencies, run a package manager, touch lockfiles, edit `MEMORY.md` or `WORK_ORDERS.md`, stage,
> commit or push. Use cohesive source and test files named by behaviour. If the sandbox prevents a
> run, report `TESTS NOT RUN: <reason>`. Report `RESULT: BLOCKED <reason>` only when the code itself
> cannot be completed. Start with `PLAN:`, narrate progress briefly and finish with `RESULT: DONE`
> or `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

> **Implementer: stop here.** The Orchestrator owns dispatch, reviews, evidence runs, version release,
> register updates and landing.

- Follow `orchestrate-codex` for the configured implementer, Tier 3 independent review and required security evidence. Run the affected billing and permission tests through the workspace test runner, including production-equivalent concurrency evidence.
- Capture Stripe Sandbox evidence for one CHF gross-price purchase and renewal, a paid upgrade, a scheduled downgrade, both billing periods, and cancellation of a paid year with access through its end. Include the effect on the app's effective member limit. Apply existing operator authorization to the Sandbox configuration and payment actions. Record any provider limitation before completing the order.
- Link the review and test evidence, release and consumer handoff in Completion. The Cinevia integration and production launch are separate decisions.

## Gate record

Gate #1 approved by the operator 2026-11-25 ("los gehts" after the DCM billing requirements and parallel token-accounting scope were confirmed in the billing chat).
