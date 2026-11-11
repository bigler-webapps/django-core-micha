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

Pending operator approval and Orchestrator mapping.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Observed in Maana on 2026-11-12 with the local app stack and intercepted email delivery. The focused integration test set the email quota to one request and observed HTTP 201 twice. `BaseUserViewSet.get_throttles()` sets the registration throttle's scope on the instance. The installed DRF `ScopedRateThrottle.allow_request()` then replaces it with the view scope, which is unset for registration. Relevant owner files are `src/django_core_micha/auth/throttles.py`, `src/django_core_micha/auth/views.py`, and `tests/test_auth_throttles.py`. Gate #1 for shared implementation is pending.
