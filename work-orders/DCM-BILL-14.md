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

To be filled by the Orchestrator.

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
