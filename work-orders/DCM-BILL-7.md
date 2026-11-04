# DCM-BILL-7 — Checkout no longer sends `automatic_tax` by default (optional setting)

## Part A — Envelope (everyone)

**Tier:** 3 — a change inside shared-core (`django-core-micha`), billing / payment flow.
**Repository:** `bigler-webapps/django-core-micha`, trunk `main`; released as a patch version.

### Goal and expected outcome

Stripe's Managed Payments setup page lists `automatic_tax` among the parameters that are not available on a Checkout
Session created with `managed_payments[enabled]=true` (Stripe computes, collects and remits the tax itself). The shared
checkout (`stripe_service.checkout_session_params`) sends both, so Stripe may reject every checkout of every app that
uses the shared billing (Maana, Cinevia). Expected outcome: the checkout parameters omit `automatic_tax` by default and
carry it only when an app switches it on with a setting, so the default request is valid for Managed Payments.

### Settled decisions (operator, 2026-11-04)

1. `automatic_tax` becomes optional. New setting `DCM_BILLING_AUTOMATIC_TAX` (boolean), **default `False`**. When truthy
   the session carries `"automatic_tax": {"enabled": True}` exactly as today; otherwise the key is absent.
2. Everything else in the parameters stays as it is (`managed_payments`, `billing_address_collection`, `metadata`,
   `customer`, `subscription_data` / `payment_intent_data` metadata, contextual-checkout keys). Per the same Stripe
   list none of them is excluded.
3. Released as a patch version (3.20.1); the consuming apps bump their pin in their own work orders.

### Scope and acceptance

`billing/stripe_service.py::checkout_session_params`, its tests, the contract text in `docs/design/billing.md`, a CHANGELOG
entry, the version.

### Non-goals and do-not-touch

Managed Payments itself stays always on (no setting for it), the webhook and reconciliation code, plan sync (tax codes stay
on the Product), operator and withdrawal endpoints, models and migrations, any consuming app.

### Risks

Setting the option to true together with Managed Payments is exactly the combination Stripe lists as unavailable; the option
exists for a deployment that disables Managed Payments in future. The docs must say so. Apps that relied on automatic tax
being requested get none now; with Managed Payments Stripe handles the tax, so nothing is lost for the apps that exist.

### Required tests to write or update

Default parameters have no `automatic_tax` and still carry `managed_payments`, `billing_address_collection` and the metadata;
with `DCM_BILLING_AUTOMATIC_TAX=True` the key is present as `{"enabled": True}`; the checkout endpoint no longer sends
`automatic_tax` to Stripe by default; the existing "parameters are valid Stripe 16 fields" assertions stay.

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec. One dispatch; complete, no stubs.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Touch only
`src/django_core_micha/billing/stripe_service.py` and the two test files named below. Do NOT touch `pyproject.toml`,
`CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (docs, version and register are the Orchestrator's), and leave
`src/django_core_micha/scripts/sync_secrets.py` and `tests/test_sync_secrets.py` exactly as they are (another session's
uncommitted work). Run tests with `python -I -m pytest <path>` from the repo directory.

### Context package (paths under `src/django_core_micha/billing/`)

- `stripe_service.py:117-139` `checkout_session_params(...)`: the single place that builds the session; line 124 holds
  `"billing_address_collection": "required", "automatic_tax": {"enabled": True},` and line 125 `"managed_payments": {"enabled": True}}`.
  Settings are read with `getattr(settings, name, default)` (see line 109, 121). Both the plain and the contextual checkout use this builder.
- `tests/test_stripe_chunk_b.py:91-108` `test_checkout_session_parameters_match_stripe_16_checkout_fields` asserts
  `params["automatic_tax"] == {"enabled": True}` (line 104); it must change to assert the key is absent by default. Add a
  sibling test with `override_settings(DCM_BILLING_AUTOMATIC_TAX=True, ...)` asserting `{"enabled": True}` and that the
  parameters remain a subset of `SessionCreateParams.__annotations__` (same import as line 107).
- `tests/test_review_fixes.py:76-97` `test_checkout_endpoint_sends_managed_payments_and_the_app_metadata_to_stripe`: add
  `assert "automatic_tax" not in sent` and keep the rest.

### Fixed design

Build the base dict without `automatic_tax`; after it, `if getattr(settings, "DCM_BILLING_AUTOMATIC_TAX", False):
params["automatic_tax"] = {"enabled": True}`. Keep the docstring accurate (it says only supported parameters are built).
No new import, no schema change, no other behaviour change; every other key and its value stays byte-for-byte.

### Invariants

Default request valid for Managed Payments; the option is off unless an app sets it; no other parameter changes; contextual
and plain checkout stay identical in shape apart from their existing differences.

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

Gate #1: the operator decided on 2026-11-04, in the bigler-consult promotion session, to implement this and to make
`automatic_tax` optional ("Ich schlage vor, wir setzen das um und machen automatic_tax optional (je nach Bedarf)").
Trigger: Stripe's Managed Payments setup page (operator screenshot 2026-11-04) lists `automatic_tax` as unavailable;
the other 15 listed parameters were checked against `checkout_session_params` and none is sent. Not verified against a
live Stripe call: whether Stripe rejects or ignores the parameter; the staging test purchase in Maana decides.
Review: Tier 3, all lenses plus `sec_reviewer`. Orchestrator writes docs, CHANGELOG and the 3.20.1 bump after review;
the register row, then the consuming apps' pin bumps (Maana, Cinevia) as their own work orders.
