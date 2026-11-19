# DCM-BILL-14 — Allow CHF as the billing catalogue currency

## Part A — Envelope (everyone)

**Repository:** `bigler-webapps/django-core-micha`, trunk `main`.
**Tier:** 3 — a change inside shared-core, money/billing.
**Consumer:** maana MAANA-APP-42 pins the release that carries this order.

### Goal and expected outcome

Maana's prices are in USD, but its Stripe account settles only in CHF. Stripe applies Adaptive Pricing (the customer
pays in the local currency) only when the price currency is one of the account's settlement currencies, so every Maana
checkout stays in USD. A test-mode probe on 2026-11-19 with CHF prices and Managed Payments showed Adaptive Pricing
active for all four plan kinds: a German customer was offered EUR, a US customer USD, each beside CHF. Expected outcome:
an app's `billing.yaml` may declare `currency: chf`, and the whole billing core (plan sync, checkout, stored prices,
contracts, reminders, the public catalogue) uses that currency instead of assuming USD.

### Settled decisions (operator 2026-11-19, in the Maana Expertenchat)

1. **Allowed currencies:** `usd` and `chf`. Anything else is still rejected when `billing.yaml` is loaded.
2. **One currency per app:** the catalogue currency applies to every plan of the app. No per-plan or per-customer
   currency, no `currency_options`; local currencies come from Stripe Adaptive Pricing only.
3. **Currency change creates new prices:** a changed catalogue currency counts as a changed price, so the plan sync
   creates new Stripe prices in the new currency and checkout uses them. Prices in the old currency are no longer
   offered. There are no running subscriptions in production (operator 2026-11-19), so no mixed-currency stock has to be
   carried; amounts already recorded keep the currency they were recorded with.
4. **No conversion logic in dcm:** dcm never converts amounts; Stripe does at checkout.

### Non-goals

EUR or other currencies; multi-currency price points; AI provider cost accounting (stays in USD, e.g.
`assistant_usd_cent` token rates); Maana's catalogue and copy (MAANA-APP-42).

### Risks

A remaining hard-coded `usd` default would silently create or verify USD prices for a CHF app, or show wrong currencies
in contract summaries and reminders. A currency change that the sync treats as unchanged would keep the old prices.

### Required tests to write

- `billing.yaml` with `currency: chf` loads; an unsupported currency is rejected.
- Plan sync for a CHF catalogue creates and verifies CHF prices; switching an existing USD mapping to CHF creates a new
  price version and checkout uses it.
- Contract summary, continuation reminder and public catalogue report CHF for a CHF app; USD apps unchanged.

## Part B — Implementation map (implementer)

Target working directory: `C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (all paths below relative to it).
Work from this package; open only the named files to verify. Run pytest from this directory with
`python -m pytest <file> -q -p no:cacheprovider` (settings `tests.settings`, see `pyproject.toml`).

### Verified seams (src/django_core_micha/billing/)

- `config.py:44` `if raw["currency"] != "usd": raise ValueError("only usd currency is supported")` and `config.py:87`
  `return BillingConfig(app,"usd",tuple(plans),rates,contract)`: the config validates usd only and then hard-codes
  `"usd"` into `BillingConfig.currency` (field at `config.py:27`). Allow exactly `usd` and `chf` (lower-case literal, as
  today; anything else keeps raising `ValueError`) and carry the declared value into `BillingConfig`.
- `stripe_service.py:34` `def stripe_fingerprint(plan, currency="usd")`: the default hides a missing currency. Make the
  currency a required parameter (same position) and make sure every caller in this repo passes the catalogue currency
  (`sync_plans` already calls `stripe_fingerprint(plan, cfg.currency)` at ~180; check tests and other callers).
  The fingerprint already includes the currency, so a changed catalogue currency yields a new fingerprint and
  `sync_plans` creates a new price version; prove it rather than assume it.
- `stripe_service.py:527` (`_record_contract_payment`-style invoice insert): `(invoice.get("currency") or "usd")` falls back
  to usd; use the catalogue currency like the other fallbacks at `stripe_service.py:788` and `:803`
  (`load_billing_config().currency`).
- Already currency-correct (verify with tests, do not rewrite): `_verify_plan_price` / `_create_plan_price` /
  `sync_plans` take `cfg.currency` (~83-260); `contracts.py:112` summary `next_charge.currency = cfg.currency`;
  `annual_term_reminders.py:_currency` uses the purchase/payment currency; `views.py` plans/catalogue data.
- Maana's public catalogue endpoint (`maana_billing/views.py`) reads `config.currency`: so `BillingConfig.currency`
  must be the declared one.

### Do not touch

AI cost accounting (`cost_usd_micros`, `assistant_usd_cent`, `*_usd_micros`, `AI_MODEL_PRICES_USD_PER_MILLION`), models and
migrations, the version number and `CHANGELOG.md` (the Orchestrator releases), other currencies, per-plan currencies,
conversion logic, other billing behaviour. If a doc under `docs/` states that only USD is supported, correct that one
statement; no other doc work.

### Pitfalls

- Existing tests use `currency: usd` everywhere and must stay green unchanged.
- `ContractPayment`/`Purchase` currency columns keep the value recorded; no migration.
- A mixed state (existing USD `StripePrice` rows, catalogue switched to chf) is the new-version path of `sync_plans`;
  the existing reconciliation tests in `tests/test_plan_price_reconciliation.py` (helpers `config(tmp_path, currency=...)`,
  `configured(...)`) show how to drive it with a fake Stripe.

### Tests to write (the Envelope list, concretely)

New file `src/django_core_micha/billing/tests/test_chf_catalogue.py` (reuse helpers from the neighbouring tests):
config loads with `currency: chf`, `eur` and an upper-case `CHF` are rejected; plan sync for a CHF catalogue creates prices
with `currency="chf"` and verification rejects a price in another currency; an existing USD mapping switched to CHF creates
a new version and the checkout path uses the new CHF price; a contract summary (`next_charge.currency`), the annual-term
reminder payload and the catalogue (`BillingConfig.currency`) report CHF for a CHF app; the invoice fallback at
`stripe_service.py:527` yields `chf` for a CHF app; a USD app is unchanged in each of these.

### Preamble — required block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there is no separate plan
> file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the app `MEMORY.md` ONLY for
> conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit the register or `WORK_ORDERS.md` — the register entry and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests you wrote — nothing
> else.** Do NOT install dependencies, touch a lockfile, run a package manager, or tidy up stray files; if something in the
> repo state blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/
> `push` and do not run git at all — leave every change uncommitted in the working tree for the orchestrator's independent
> review. WRITE the tests the `Tests to write` section calls for AND **RUN the tests you just wrote** to confirm they
> execute and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The orchestrator
> re-runs the authoritative set + does the independent review after you finish — those are the gate; your own run does not
> count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line `PROGRESS: [<n>/<total>]
> <present-tense action>` before every relevant action (and `… done` on completion), spaced so no gap exceeds ~2 min,
> stdout unbuffered, plus exactly one final `RESULT: DONE|BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Tier 3: all lenses, `sec_reviewer` (billing). Release as a minor version; MAANA-APP-42 pins it. Probe evidence:
`maana/.claude/scratch/adaptive_probe_chf.py` (untracked, never staged), run by the operator 2026-11-19: all 8 sessions
`currency=chf`, `adaptive_pricing={"enabled": true}`; hosted pages showed 14,60 EUR / 13,00 CHF (DE token package) and
17,34 USD / 14,05 CHF (US token package) with a currency choice.

## Gate record

Gate #1 approved by the operator 2026-11-19 in the Maana Expertenchat (session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce): "Ja, beide freigeben und beauftragen", after choosing "Erst CHF-Probe",
"Umgerechnet, gerundet" and "Keine laufenden Abos".
