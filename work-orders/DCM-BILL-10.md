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

Pending operator approval and Orchestrator mapping.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Maana's checkout delegates to the shared session builder without a locale option. Stripe documents the `locale` Session parameter. Gate #1 for shared implementation is pending. [Stripe Checkout Sessions API](https://docs.stripe.com/api/checkout/sessions/create)
