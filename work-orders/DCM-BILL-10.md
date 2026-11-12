# DCM-BILL-10 — Forward the checkout language to Stripe

## Part A — Envelope

**Target:** `bigler-webapps/django-core-micha`, `main`.

**Tier:** 3, shared billing and checkout.

**Consumer:** [MAANA-UX-1](https://github.com/bigler-webapps/maana/blob/develop/work-orders/MAANA-UX-1.md).

### Expected outcome

A consumer can explicitly request German, English or French for hosted Stripe Checkout through the shared checkout flow. Both checkout entry points in the consumer can use the same validated option. Existing clients that omit it keep the provider's normal language selection.

### Scope

Carry a validated, optional Checkout Session locale through the existing shared checkout request and session parameter builder. Accept `de`, `en` and `fr` and reject invalid explicit values through the normal validation contract. Preserve existing authorization, plan and schedule inputs, contextual idempotency, prices, tax disclosure and checkout activation gates. Seller authority and legal receipt wording are tracked separately by MAANA-APP-40. Use Stripe test mode for provider verification.

### Required tests

- Assert that DE, EN and FR reach the Stripe Session as locale values, omission keeps the current provider default, and invalid values fail validation before calling the provider.
- Pin authorization, existing plan and schedule parameters, and idempotent retry behavior for the existing checkout paths.
- Use the provider test environment to verify the localized Session and record which contract phases hosted checkout displays.

### Risk

A language option can change Stripe's Session parameter set during a retry. The test coverage must preserve the existing idempotency guarantees. The hosted page can render the initial recurring price differently from Maana's two-phase explanation even when the schedule is correct.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Start from the workspace root. This scope owns billing source/tests and its contract documentation.

- `src/django_core_micha/billing/views.py::checkout, _contextual_checkout`: validate the optional locale once before Stripe calls or customer creation. Keep auth, CSRF and response envelopes. The simple and contextual branches share `stripe_service.py::checkout_session_params`. Existing omitted-locale requests must continue to omit the provider field.
- A contextual attempt has a persisted Stripe price and a stable Stripe idempotency key derived from the opaque public attempt reference. Both existing-attempt and concurrent-create recovery must prevent a changed locale from reaching Stripe under that key, including retries after an ambiguous provider failure. Inspect `billing/models.py::CheckoutAttempt` and `billing/tests/test_checkout_attempts.py`. The work order authorizes the billing behavior; leave schema changes for explicit approval if needed. Assess a versioned opaque reference that persists the locale without schema changes, preserves entropy and fits the field. Preserve legacy reference interpretation, full-key uniqueness, and unchanged omitted-locale retries.
- Extend the existing checkout attempt tests for both checkout modes, provider call arguments, unchanged retries after a timeout, incompatible locale, and input rejection before side effects. Document the contract in `docs/design/billing.md` and the repository API contract if present. Leave Stripe credentials, external test sessions and release work to the Orchestrator.

### Implementer preamble

> This file supplies the complete spec. Run the workspace hook canary and use the parent-confirmed fresh main checkout. Read nearby governance and the backend skill for conventions. Edit the named source/test/contract paths and run the tests you wrote once. Do not read secrets or .env, install dependencies, update MEMORY.md or WORK_ORDERS.md, self-review, or run git add, commit or push. Leave the diff for independent review and the authoritative Orchestrator test run.
>
> Start with `PLAN: <steps>`. Emit `PROGRESS: [n/total] <action>` within two minutes of relevant steps. End with exactly one `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Maana delegates checkout to the shared session builder. Stripe documents the `locale` Session parameter. Gate #1 was approved by the operator on 2026-11-12. Dispatch the configured implementer, then run the independent Tier 3 billing review and affected tests. [Stripe Checkout Sessions API](https://docs.stripe.com/api/checkout/sessions/create)

The candidate is local in `billing/views.py`, `billing/stripe_service.py`, `billing/tests/test_checkout_attempts.py`, and `docs/design/billing.md`. The existing billing design document is this library's API contract. A versioned attempt reference retains the locale and the full random token without a schema change. Existing references represent omitted locale. The affected suite passed 204 tests with one skip. Independent review covered the five Tier 3 lenses. Stripe test mode still needs a sanctioned test route to verify the hosted language and visible contract phases. The runtime rejected an availability probe because it inspected a key prefix. The probe did not run.
