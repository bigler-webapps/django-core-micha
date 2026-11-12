# DCM-AUTH-14 — Enforce the shared signup email rate limit

## Part A — Envelope

**Target:** `bigler-webapps/django-core-micha`, `main`.

**Tier:** 3, shared authentication and abuse controls.

**Consumer:** [MAANA-UX-2](https://github.com/bigler-webapps/maana/blob/develop/work-orders/MAANA-UX-2.md).

### Expected outcome

Shared registration and resend requests consume the configured per-email quota. Case variants of the same address share a quota across clients. The endpoint returns HTTP 429 when that quota is exhausted and sends mail only for accepted requests. Clients keep using the existing registration endpoint and confirmation flow.

### Scope

Restore the configured target scopes on the existing DRF auth throttles. Verify the sibling per-access-code and recovery throttles where they use the same base class. Keep the configured rates, client/IP throttles, signup proof, registration response and confirmation contract. Use the existing cache and hashed identifiers. Scope is limited to rate-limit enforcement and its tests.

### Required tests

- Send ordinary registration requests from separate clients to the same address with different letter case. Assert that the configured limit returns HTTP 429 and the rejected request sends no email. A different address keeps its own quota.
- Pin the existing behavior of the affected per-access-code and recovery throttles, including missing fields and use alongside the client/IP throttle.
- Check that requests for new and existing addresses still receive equivalent registration responses and confirmation preserves existing account ownership.

### Dependency and risk

Maana can enable the resend path after its exact dependency pin includes the shared fix and the consumer integration test passes. The other consumers of the shared throttles may start enforcing the configured limits once they adopt the release, so the affected auth tests must exercise the configured scopes as well as the keys.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Start from the workspace root. This scope owns auth throttle source and related tests only.

- `src/django_core_micha/auth/throttles.py::_BodyKeyedScopedRateThrottle`: the inherited DRF request method replaces the explicitly assigned instance scope with the view's scope. Keep named configuration, cache key normalization, DRF rate/wait mechanics, and behavior when the identifier is absent. Check call sites in `auth/views.py::BaseUserViewSet.get_throttles`, `RecoveryRequestViewSet.get_throttles`, and `invitations/views.py::AccessCodeViewSet.get_throttles`.
- `tests/test_auth_throttles.py` and a focused registration request test in `tests/`: use the existing test app settings and mock the delivery boundary. Exercise real dispatch and response behavior as well as the key. Preserve proof and confirmation behavior. Coordinate neither edits nor tests with the parallel billing lane.

### Implementer preamble

> This file supplies the complete spec. Run the workspace hook canary and use the parent-confirmed fresh main checkout. Read nearby governance and the backend skill for conventions. Edit the named source/test paths and run the tests you wrote once. Do not read secrets or .env, install dependencies, update MEMORY.md or WORK_ORDERS.md, self-review, or run git add, commit or push. Leave the diff for independent review and the authoritative Orchestrator test run.
>
> Start with `PLAN: <steps>`. Emit `PROGRESS: [n/total] <action>` within two minutes of relevant steps. End with exactly one `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Observed in Maana on 2026-11-12 with intercepted email delivery. DRF replaced the named registration throttle scope with the unset view scope. Gate #1 was approved by the operator on 2026-11-12. Dispatch the configured implementer, then run the independent Tier 3 auth reviews and affected tests.
