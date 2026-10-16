# DCM-BILL-1 — Shared subscriptions and AI tokens with Stripe Managed Payments

## Part A — Envelope (everyone)

**Tier:** 3 — money and billing in the shared core, webhooks from an external provider, new secrets, new models. **Repository:** `bigler-webapps/django-core-micha` (canonical; the ui-core-micha side is UCM-BILL-1). **Consumers:** Maana (MAANA-APP-8), Cinevia (CIN-BILL-1); bigler-consult reads events and cost reports (BCO-BILL-1).

### Goal and expected outcome

Several apps of bigler-consult are to be sold as subscriptions, Maana additionally with AI tokens. Each app should not build payment on its own. The shared core provides plans, checkout, subscription state, a token account and the recording of AI costs once; an app declares its plans in a file and switches the feature on.

Expected outcome: an app can sell its plans through Stripe, knows for every person whether they have access and how many tokens are left, debits tokens for AI work, and reports revenue-relevant events and AI costs per app.

### Settled decisions (operator, 2026-10-16)

1. **Seller and provider:** bigler-consult (sole proprietorship) sells through **one Stripe account** with **Managed Payments** (Stripe is merchant of record and handles VAT/sales tax; checkout only through Stripe Checkout or Payment Links). Every Stripe object carries the metadata `app=<app>`.
2. **Plans as code:** each app declares its plans, packages and token rates in a `billing.yaml` (price, currency USD, billing period, term, token allowance, seats, tax code). A management command synchronises Products and Prices to Stripe idempotently (lookup keys); an existing Price is never changed, a change creates a new Price; existing subscriptions keep theirs.
3. **Plan kinds:** personal subscription (monthly or yearly), team package with a fixed number of seats and a fixed term (e.g. 6 months, prepaid), token package (one-time purchase). Larger teams are granted manually by the operator.
4. **Access:** a person has access while they hold an active personal subscription, a seat in an active team package, or an operator-granted free access („Ohne Abo freigeschaltet"). Without access, the app shows its content read-only and keeps export available; data is deleted 90 days after access ended, after an e-mail notice at least 14 days before.
5. **Tokens:** an account (person or team) holds a token ledger. The plan allowance is granted per term at its start (yearly and team allowances in full at once) and expires at the end of the term; purchased packages do not expire while access exists. Debits are atomic, never below zero, and recorded with app, purpose, amount and reference.
6. **AI cost recording:** every AI call made through the core records provider, model, input/output/cache tokens and cost in USD (existing cost table `AI_MODEL_COST_TABLE_CENTS_PER_1K`). Apps convert cost or units into tokens with the rates from `billing.yaml`.
7. **Webhooks:** the app's endpoint verifies the Stripe signature, processes each event idempotently, ignores events whose `app` metadata is another app, and reconciles from the Stripe API when events were missed.
8. **Customer portal:** cancelling, payment method and receipts through Stripe's customer portal.
9. **Cost report:** a read-only, authenticated endpoint per app returns monthly AI costs and tokens used (for bigler-consult, BCO-BILL-1); token-scoped, no personal data beyond counts.
10. **Test mode on staging and local:** Stripe test keys; production keys only in production. Secrets are declared in each app's `secrets.yaml` and arrive through `sync-secrets`.

### Scope and acceptance

1. Models, services and API for plans (from `billing.yaml`), checkout session, customer portal link, webhook handling, subscription/seat/free-access state, token ledger with grants, debits, purchases and expiry, AI cost records, cost report.
2. Sync command and a check that fails on an inconsistent `billing.yaml`.
3. Opt-in per app; an app without billing behaves as today.

### Non-goals and do-not-touch

The UI (UCM-BILL-1), app-specific pricing and gating (per app WO), own API keys of users (later DCM-BILL-2), invoices outside Stripe, Stripe Connect.

### Risks

Wrong access decisions cost money or lock out paying people: webhook handling must be idempotent and verifiable; reconciliation repairs missed events. A token ledger that can go negative or double-debit loses money silently. Secrets must never appear in logs or responses.

### Required tests to write or update

Signature verification and idempotent replay; every subscription lifecycle event (created, renewed, payment failed, cancelled, ended) mapped to access; team seats; free access; token grant, expiry, purchase, concurrent debits; foreign `app` events ignored; reconciliation; cost recording and report scope; `billing.yaml` validation and sync idempotency (Stripe mocked).

## Part B — Implementation map (implementer; Orchestrator completes before dispatch)

Placeholder — dispatch-blocking. The Orchestrator fills the context package, target directory, progress contract and implementer preamble before any dispatch.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 pending. Shared-core change: release, version and consumer pin bumps follow the dcm publish flow. Seams: `extraction/cost_guard.py`, the assistant `turns.py`/`credentials.py` (billing source), settings conventions. Tier 3: all lenses plus `sec_reviewer`. Register `BILL-*` in SHARED_CAPABILITIES once landed.
