# DCM-BILL-15 — Subscriptions for app objects

## A. Envelope

**Tier:** 3 (shared billing, access and schema). **Target:** `django-core-micha`, `main`. **First consumer:** Cinevia (`CIN-BILL-2`).

### Goal and expected outcome

An app can sell a recurring subscription for one of its objects. In Cinevia, one person pays for a film project and the subscription determines that project's member limit. The app can read the project's paid or granted access and apply its own membership rules.

### Existing capabilities

DCM already provides a per-app `billing.yaml` catalogue, versioned Stripe price sync, Checkout, Customer Portal, verified webhooks, reconciliation, operator grants and contract declarations. Maana uses the existing personal and team billing flows with a recurring token allowance or shared token pool. UCM's subscription and withdrawal views are account-oriented. The workspace catalogue records both cores in `webapp-management/SHARED_CAPABILITIES.md`.

### Scope and acceptance

- **Catalogue.** Declare a distinct object subscription plan kind in `billing.yaml` with a monthly or annual interval, a positive member limit and an amount in the app's configured currency. Monthly and yearly variants have separate plan keys. Prices are final amounts including applicable VAT. Validate the new kind and sync it using the existing Stripe Managed Payments integration. For Cinevia, verify that Swiss CHF checkout and recurring invoices match the advertised gross amounts. When a tariff change creates a prorated charge, verify its calculation separately.
- **Object and permissions.** Identify an object by the app key and a stable opaque object key. The app establishes that the object exists and authorizes purchases and state reads before exposing those operations to its users. Cinevia allows eligible project admins to start a purchase when no existing subscription or attempt blocks it. Only the payer may open the object portal, change the plan, cancel or exercise other contract rights for that purchase. The payer retains these rights after leaving the project. The object portal targets that subscription and its app's eligible object plans, including after the action completes. It cannot expose contract actions for a different object or a personal or team subscription. The existing account portal cannot change or cancel object subscriptions.
- **Checkout.** Each object can have at most one active access subscription and one in-flight purchase attempt for that access, with a new purchase refused while either blocks it. Both existing checkout paths, with and without a checkout context, reject object plan keys before creating a Stripe session. Parallel requests from different eligible buyers, retries and delayed payments must converge without a duplicate Stripe subscription or charge. An expired or definitively failed attempt releases the object for another purchase. A return from Checkout alone cannot grant access. A verified Stripe event or reconciliation confirms the payment.
- **Plan changes.** Classify a change by the member limit, regardless of the billing interval or price. The payer uses Stripe to review and confirm the target plan and any charge. Apply the following rules even when Stripe's standard portal classifies a change differently.

  | Target member limit | Billing interval | When the plan, billing and limit change |
  | --- | --- | --- |
  | Higher | Same or different | After confirmation of any amount due. Show the credit for unused paid time, the amount due and the new billing period before confirmation. For an interval switch, the new billing period starts when the change takes effect. A failed charge leaves the prior plan and limit in force for their paid term. |
  | Lower | Same or different | At the end of the paid period. Keep the current plan and limit until then. Bill the new plan at that boundary, with no early charge for the switch. |
  | Same | Monthly to yearly or yearly to monthly | At the end of the paid period. Bill the new interval at that boundary, with no early charge for the switch. Keep the member limit. |

- **Ordinary cancellation.** The payer can stop renewal at the end of the paid month or year, including when Stripe has already scheduled a downgrade or interval switch. Where the Stripe portal blocks cancellation, the payer can submit an ordinary cancellation through the existing DCM contract entry point for that purchase. Cancellation supersedes the planned change and prevents both a renewal charge and a charge for the scheduled plan. Access remains through the paid term. Provider events and reconciliation agree on the effective plan, member limit and cancellation even with duplicate or out-of-order delivery.
- **Object state and grants.** Provide an app-facing state query with the source (subscription, operator grant or none), plan key and name, member limit, billing status, paid-through date, renewal or cancellation status and payer when available to the app. Authorized operators can grant and revoke unlimited object access with a required reason and an audit record that identifies the operator. A grant controls effective access independently of the paid contract and preserves the paid subscription's details. The operator overview reports object subscriptions separately from personal account counts.
- **Contracts and tokens.** Keep the purchaser as the contract holder and apply the app's existing configured contract, withdrawal and refund policies to the object purchase. Use the ordinary cancellation rule above for its effective date, even when Stripe already has a scheduled change. A valid withdrawal ends the paid access tied to that contract and refunds its attributable payments according to the configured policy. Events and reconciliation must not restore withdrawn access. Keep token purchases and renewed token allowances independent of project access purchases, including when the same Stripe customer owns both. A cancellation, withdrawal or refund affects only the associated purchase, payment and entitlement. In particular, the one-checkout rule must permit a separate token purchase and must not block renewal of an existing subscription with token allowances. Conversely, ending an unrelated token-bearing contract must leave the project subscription in place.
- **Compatibility and delivery.** Preserve existing personal subscriptions, team packages, team seats, tokens, grants, withdrawal and contract behaviour and existing API response keys. Object subscriptions grant no personal access and have no implicit token allowance. Deliver additive migrations, consumer-facing billing contract documentation, a changelog entry and a minor DCM release suitable for pinning by Cinevia.

### Exclusions

Cinevia owns project membership, invitation limits, free-tier eligibility and read-only enforcement. The first consumer work order owns app integration, project settings, UI, classification of existing projects and go-live. This shared-core order covers compatibility with existing token offerings. It does not add a Cinevia token product, a new token plan kind, a payer-transfer flow, a company licence, or changes to another app's plans or CHF prices.

### Risks

- An incorrect subject or plan association can grant access to a different project or to the payer personally.
- A local uniqueness check after payment cannot prevent a second charge. Provider delays and concurrent buyers must be covered at checkout creation and through payment confirmation.
- Using the whole Stripe customer as the billing scope can affect other subscriptions or token credits. Portal, contract actions, refunds and reconciliation need purchase-level scope.
- Stripe's [portal upgrade and downgrade conditions](https://docs.stripe.com/customer-management/configure-portal#configure-subscription-management) use Stripe price and interval rules. The [portal also blocks cancellation](https://docs.stripe.com/customer-management#limitations-to-modifying-subscriptions) while an update is scheduled. Demonstrate both mixed-interval changes and cancellation of a scheduled change before relying on a provider flow.
- The existing Stripe price sync verifies exclusive tax behaviour. Gross pricing for the new plans and the required portal transitions depend on actual Managed Payments behaviour. Sandbox evidence is required for these paths.

### Required tests to write

- Catalogue and sync: reject invalid intervals and non-positive member limits, check gross-price semantics for object plans, preserve existing price semantics and verify the active Stripe price mapping.
- Checkout and permissions: reject unknown objects at the app authorization boundary, reject a non-authorized buyer, isolate identical object keys across apps and reject access to another payer's portal or contract. Confirm both existing checkout paths reject an object plan key before calling Stripe. Exercise concurrent starts by two eligible buyers, expiry, delayed completion and retry after definitive failure. Confirm at most one Stripe purchase and no entitlement on browser return alone.
- Portal and contract scope: use a single payer with subscriptions for two objects and a personal subscription with a recurring token allowance. From one object's portal, verify that only that subscription and eligible targets are actionable, including after completion. From the existing account portal, verify that neither object subscription can be changed or cancelled and that the personal actions still work. Verify that a former project member who paid can manage that contract and another project admin cannot.
- Lifecycle: confirm purchase and renewal, higher and lower limits with and without an interval change, and same-limit month-to-year and year-to-month switches. Assert the charge, billing period and effective member limit before and after each change. Include a failed upgrade charge. Cover cancellation at monthly and annual expiry, including cancellation after scheduling a downgrade. Verify the scheduled change does not take effect, no further charge is made and access expires at the paid boundary. Exercise duplicate and reordered events and reconciliation, including a stale event after withdrawal.
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
- Verify the provider flows early in Stripe Sandbox. Capture CHF gross-price purchase and renewal, a paid upgrade with proration, both directions of a same-limit interval switch, and a downgrade whose target invoice amount is higher (Cinevia Gross monthly to Klein yearly). Confirm the timing of the charge and member limit. Schedule a downgrade on a paid annual subscription, then cancel it while the change is still pending. Verify the paid access end date and absence of a renewal or scheduled-change charge. Verify the two-object and personal-subscription portal boundaries. Apply existing operator authorization to Sandbox configuration and payments. If the selected provider flows cannot satisfy the acceptance rules, return the conflicting case for a decision before implementation proceeds on that flow.
- Align the `CIN-BILL-2` handoff with the payer rule. Eligible project admins can start the initial purchase or buy again when the object is eligible. Changes and cancellation belong to the payer. The Cinevia integration must provide an account-level route for that payer after project departure. Link the review and test evidence, release and consumer handoff in Completion. The Cinevia integration and production launch are separate decisions.

## Gate record

Original Envelope (`286df2ba`): the operator approved Gate #1 on 2026-11-25 ("los gehts" after the DCM billing requirements and parallel token-accounting scope were confirmed in the billing chat).

The operator requested the review findings be integrated on 2026-11-25. Gate #1 for the revised Envelope is pending. Confirm the same-limit interval rule and the cancellation route before dispatching implementation.
