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
4. **Access:** a person has access while they hold an active personal subscription, a seat in an active team package, or an operator-granted free access („Ohne Abo freigeschaltet"). Without access, the app shows its content read-only and keeps export and download available. **Amendment (operator, 2026-10-16): no automatic deletion for now** — data stays read-only without a deletion date; a later deletion policy is a separate decision.
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

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the module layout, the names and shapes that UCM-BILL-1 (UI), MAANA-APP-8 and CIN-BILL-1 build against, and the invariants. It is implemented in TWO chunks (two dispatches, A then B, same working tree); each chunk must be complete: no stubs, no skeleton, every bullet implemented and tested. Money is involved: when in doubt, deny access and never write below zero.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories. Run tests with the interpreter named in your dispatch note (it has Django 6.1, `stripe` 16, PyYAML, psycopg 3), never the global python.

### Module and packaging (already prepared by the Orchestrator, do not edit)

New opt-in app `django_core_micha.billing` with `AppConfig(name="django_core_micha.billing", label="dcm_billing", default_auto_field=BigAutoField)`. NOT part of `CORE_APPS`: an app that does not install it gets no tables and no routes. `tests/settings.py` already lists it in `INSTALLED_APPS` and maps `"dcm_billing": None` in `MIGRATION_MODULES` (the Orchestrator generates `0001_initial` after chunk A and removes that override), `pyproject.toml` has the extra `billing = ["stripe>=16", "PyYAML"]`, and `tests/test_declared_dependencies.py` knows `stripe`. Import `stripe` and `yaml` lazily inside functions that need them (the module must import without the `billing` extra; a missing extra raises an `ImportError` naming `django-core-micha[billing]`, like `assistant/turns.py` does for `[ai]`). Reuse `django_core_micha.auditlog.register(model, redact_fields=...)` from `billing/audit_config.py` (see `assistant/audit_config.py`): register the models, redact `stripe_*` ids and e-mail-like fields. No Django admin registration (the admin UI is forbidden). Put tests in `src/django_core_micha/billing/tests/` and add that directory to `testpaths` in `pyproject.toml` (the one pyproject edit allowed; `tests/test_testpaths_coverage.py` enforces it).

### Settings (read with `getattr(settings, ..., default)`, never log values)

`DCM_BILLING_APP` (str, the app key written into every Stripe object's metadata `app`; required when the module is installed, system check error if empty), `DCM_BILLING_FILE` (path to `billing.yaml`; default `BASE_DIR / "billing.yaml"`), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `DCM_BILLING_SUCCESS_URL`, `DCM_BILLING_CANCEL_URL`, `DCM_BILLING_PORTAL_RETURN_URL`, `DCM_BILLING_REPORT_TOKEN_SHA256` (list of lowercase hex SHA-256 digests; the cost-report token is compared by digest, constant time), A test-mode key (`sk_test_`) versus live key is not interpreted by the core.

### `billing.yaml` (schema, validated; one file per app)

```yaml
app: maana                  # must equal DCM_BILLING_APP
currency: usd               # only usd in v1
plans:
  - key: personal_monthly   # [a-z0-9_]{1,40}, unique
    kind: subscription      # subscription | team_package | token_package
    name: "Personal monthly"
    amount_cents: 1500
    interval: month         # month | year  (subscription only)
    token_allowance: 100000 # tokens granted per term (subscription, team_package); default 0
    tax_code: txcd_10103001 # Stripe tax code, required
  - key: team_6m_5
    kind: team_package
    name: "Team, 5 seats, 6 months"
    amount_cents: 60000
    term_months: 6          # team_package only, prepaid fixed term
    seats: 5
    token_allowance: 600000
    tax_code: txcd_10103001
  - key: tokens_100k
    kind: token_package     # one-time purchase
    name: "100k tokens"
    amount_cents: 1000
    tokens: 100000          # token_package only
    tax_code: txcd_10103001
token_rates:                # how an app converts units into tokens (integer tokens per unit)
  chat_1k_input_tokens: 10
  chat_1k_output_tokens: 40
```
Validation (one function used by the system check, the sync command and tests, returning a typed `BillingConfig`): unknown keys, duplicate plan keys, wrong kind-specific fields, non-positive amounts or counts, app mismatch, unsupported currency, missing tax code, non-integer rates are errors. A Django system check `dcm_billing.E001..` reports them (only when the module is installed); `manage.py billing_check` prints them and exits non-zero.

### Domain contract (fix these names; design the columns yourself within them)

Models (all in `billing/models.py`, one migration for all of chunk A and B): `BillingCustomer` (user, `stripe_customer_id` unique), `StripePrice` (app, plan_key, `version`, `lookup_key` unique, `fingerprint`, `stripe_product_id`, `stripe_price_id`), `Subscription` (user, plan_key, `stripe_subscription_id` unique, `status`, `current_period_start/end`, `cancel_at_period_end`, `ended_at`), `TeamPackage` (owner user, plan_key, seats, `starts_at`, `ends_at`, `stripe_ref` unique) and `TeamSeat` (package, user; a user holds at most one active seat per package; seat count never exceeds `seats`), `FreeAccess` (user, granted_by_label, reason, `granted_at`, `revoked_at`), `TokenAccount` (exactly one of user or team package; unique per owner), `TokenEntry` (append-only ledger row: account, kind `grant|purchase|debit|expiry|adjustment`, signed `amount`, `expires_at` for grants, `remaining` for grants and purchases, `purpose`, `reference`, `created_at`; unique `(account, kind, reference)` for idempotency when a reference is given), `StripeEvent` (`event_id` unique, type, `status`, received/processed timestamps, error text without secrets), `AICostRecord` (provider, model, user nullable, `input_tokens`, `output_tokens`, `cache_tokens`, `cost_usd_micros` integer (millionths of a USD), `purpose`, `reference`, `created_at`).

Services (public Python API for apps, module `billing/services.py` or split modules re-exported from `billing/__init__`-free paths):
- `access_state(user) -> AccessState(status: "active"|"read_only", source: "subscription"|"team"|"free"|None, ends_at)`: `active` while the user holds an active (or past_due within Stripe's retry) personal subscription whose period has not ended, OR a seat in a team package that has started and not ended, OR a non-revoked `FreeAccess`. Otherwise `read_only` (`ends_at` is when access ended, never a deletion date; operator amendment 2026-10-16: no automatic deletion, no notice, no `none` state). Subscription `status` mapping: `active|trialing|past_due` → access, `canceled|unpaid|incomplete_expired` → ended, `incomplete|paused` → no access. Choose the latest ending source.
- Tokens: `token_balance(owner)`, `grant_tokens(owner, amount, *, expires_at=None, reference, purpose)`, `credit_purchase(owner, amount, *, reference)`, `debit_tokens(owner, amount, *, purpose, reference)` (raises `InsufficientTokens`, never writes a negative balance, atomic with `select_for_update` on the `TokenAccount` row inside `transaction.atomic`, consumes expiring grants first by earliest expiry, then purchases; idempotent per `(account, "debit", reference)`; two concurrent debits can never both succeed past the balance — prove it with a threaded test on a database that supports row locks if the test database allows, otherwise with an explicit interleaving test of the service functions), `expire_tokens(now=None)` (writes `expiry` rows for ended grants; expired tokens are never spendable even before that command runs), and `tokens_for(rate_name, units)` (round up, uses the loaded `token_rates`). Grants per term: the plan allowance is granted at the start of each term (monthly subscription: every paid period; yearly subscription and team package: the whole allowance at once at the start) and expires at the term end; purchased packages do not expire while the owner has access.
- AI cost recording: `record_ai_call(*, user, provider, model, input_tokens, output_tokens, cache_tokens=0, purpose, reference="")` computes the cost with the existing cost table (`extraction.cost_guard.estimate_cost_cents`/`_get_cost_table`; use a micro-USD result with the same per-1k rates, add a small public accessor to `cost_guard.py` if needed, no behaviour change there) and stores an `AICostRecord`. It never raises into the caller's request path for a database or table problem beyond logging (cost recording must not break a chat), but it is exact when it succeeds. Integration points, each guarded by `apps.is_installed("django_core_micha.billing")` and neutral when billing is not installed: `assistant/turns.py` `run_turn` (one record per completed or failed turn that reported usage: provider = driver, purpose `chat`, reference = turn id, user = sender), `assistant/completion.py` `acomplete` (purpose `completion`), `extraction/service.py` after settlement (purpose `extraction`). Add one test per integration point and one proving that an app without the module installed is unaffected (the existing assistant and extraction tests are part of the Orchestrator's run).
- Cost report: `GET <prefix>/cost-report/?month=YYYY-MM` (default: the current UTC month), `Authorization: Bearer <token>`, token checked against `DCM_BILLING_REPORT_TOKEN_SHA256` in constant time, wrong or missing token → 401 with no detail, no session auth. Response `{"app","month","calls","ai_cost_usd_micros","input_tokens","output_tokens","cache_tokens","tokens_debited","by_model":[{"provider","model","calls","cost_usd_micros"}]}`: counts and sums only, no user ids or e-mails.
- Operator commands: `billing_grant_free_access <user-id|email> --reason "..."` and `billing_revoke_free_access` (operator CLI; no admin UI), `billing_expire_tokens`.

### Chunk A (first dispatch): domain without Stripe network code

Everything above except the Stripe-facing items: `billing.yaml` loader and validation + system check + `billing_check`, models, services (access, seats, tokens, costs), cost report endpoint, free-access commands, audit registration, integration points for AI cost recording, tests for all of it with no `stripe` import needed. REST for chunk A: `GET <prefix>/me/` (authenticated; `{"access":{status,source,ends_at},"tokens":{"balance":int},"plans":[{key,kind,name,amount_cents,interval|term_months|seats|tokens,token_allowance}]}` from the loaded config) and the seat endpoints `GET/POST/DELETE <prefix>/team/seats/` (a team package owner assigns and removes seats by user id or e-mail; seat limits enforced; only the package owner may change seats). `urls.py` exposes `urlpatterns` (an app mounts it under its own prefix, like `assistant/urls.py`).

### Chunk B (second dispatch, after A is in the tree): Stripe

Sync command `billing_sync_plans [--dry-run]`: for each plan ensure a Stripe Product (metadata `app`, `plan_key`) and a Price created with a `lookup_key` of the form `<app>:<plan_key>:v<version>`; the fingerprint of the commercial fields (amount, currency, interval, kind, tax code) decides: unchanged → no call; changed → a NEW Price (`version + 1`) is created and the old one is left alone (existing subscriptions keep theirs), never an update of an existing Price; idempotent on re-run; `--dry-run` calls nothing. Checkout: `POST <prefix>/checkout/` `{plan_key}` → `{"url"}` creates a Stripe Checkout Session (mode `subscription` for subscription and team packages is a subscription only for `subscription` kind; team packages and token packages are one-time `payment` sessions) with `metadata={"app","plan_key","user_id"}` (and `subscription_data.metadata` for subscriptions), the customer from `BillingCustomer` (created on first use), success/cancel URLs from settings, compatible with Stripe Managed Payments (Stripe Checkout only, Stripe is merchant of record): isolate the session parameters in ONE function and check each parameter name against the installed SDK; do not invent a parameter, and list in your final report anything about Managed Payments you could not verify. Portal: `POST <prefix>/portal/` → `{"url"}` (billing portal session for the user's customer, return URL from settings; 404 `no_customer` when none). Webhook: `POST <prefix>/webhook/` (csrf-exempt, no auth, signature only): verify with `stripe.Webhook.construct_event(payload, header, STRIPE_WEBHOOK_SECRET)` (default tolerance), invalid signature or payload → 400 with an empty body and no logging of the payload or header; store `StripeEvent` first (unique `event_id`; a duplicate returns 200 without side effects) and process inside one transaction; ignore (200, status `ignored`) any event whose `metadata.app` (read from the event object or, for invoices, from the subscription/lines metadata) is not `DCM_BILLING_APP`; handle `checkout.session.completed` (subscription → create/update `Subscription` and grant the period allowance; team package → create `TeamPackage`, grant the allowance to the team account; token package → `credit_purchase`), `customer.subscription.created|updated|deleted`, `invoice.paid` (renewal: new period, grant the period allowance exactly once per period via the ledger reference) and `invoice.payment_failed` (status `past_due`), each mapped to the access rules above; unknown event types are stored as `ignored`. Processing failures roll back, store the error class (not the payload) and return 500 so that Stripe retries. Reconciliation: `billing_reconcile [--since]` lists Stripe subscriptions and recent events for this app (metadata filter on the client side) via the API and applies the same handlers idempotently, repairing missed events. Write `docs/design/billing.md` as the binding contract (settings, `billing.yaml`, endpoints and JSON shapes, event mapping table, token ledger rules, retention, operator commands, security notes) because UCM-BILL-1 and the app orders read it. Do NOT edit `CHANGELOG.md`, `pyproject.toml` (except the `testpaths` line in chunk A), `WORK_ORDERS.md` or `webapp-management`.

### Tests (per chunk; Stripe is always mocked, no network)

Cover every item of Part A "Required tests to write": signature verification and idempotent replay (build real signatures with `stripe.WebhookSignature`/hmac so a wrong signature is rejected), every subscription lifecycle event mapped to access, team seats (limit, removal, non-owner refused), free access, token grant/expiry/purchase/concurrent debits and an idempotent debit, foreign `app` events ignored, reconciliation, cost recording per integration point and the report scope (no personal data, 401 paths), `billing.yaml` validation and sync idempotency including "a changed price creates v2 and never updates v1". New test files only.

### Invariants

Webhook handling is idempotent and verifiable; no code path produces a negative token balance or a double debit/grant; no secret, signature header or payload ever reaches a log line, an exception message or a response; access decisions fail closed; an app that does not install the module behaves exactly as before; no Django admin; no network call in tests.

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

Gate #1 approved by the operator 2026-10-16 (answer in the django-core-micha Orchestrator session). Shared-core change: release, version and consumer pin bumps follow the dcm publish flow. Seams: `extraction/cost_guard.py`, the assistant `turns.py`/`credentials.py` (billing source), settings conventions. Tier 3: all lenses plus `sec_reviewer`. Register `BILL-*` in SHARED_CAPABILITIES once landed.
