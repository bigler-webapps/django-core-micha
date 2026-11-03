# DCM-SEC-4 — Rate limits identify the client by Cloudflare's address, not by a forgeable header

Prepared: 2026-11-02. Target repository: `django-core-micha` (`main`, minor release). Origin: `cockpit/CKP-OBS-47`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

Every app on the shared core identifies an anonymous or webhook sender for rate limiting by the address Cloudflare reports (`CF-Connecting-IP`), and by the connection's own address where that header is absent (local development). A sender can no longer change or borrow its rate-limit identity by setting `X-Forwarded-For`. DRF throttles and allauth's rate limits use the same resolution.

## Why

Residual risk named by `cockpit/CKP-OBS-46` (2026-10-13): `NUM_PROXIES` is unset, so DRF identifies an anonymous or webhook sender by the raw `X-Forwarded-For` value, which a sender can rotate (no limit) or copy from GitHub/Kuma (lock the real sender out). The DRF settings come from `django_core_micha/settings/settings_base.py`, and every app runs behind the same Cloudflare tunnel and Traefik chain, so the fix belongs here once, not per app. dcm already resolves the client address this way for the audit log (`auth/_audit_helpers.py:_client_ip`).

## Tier

Tier 3: a change inside the shared core, on a security surface (abuse protection of public endpoints).

## Operator decisions this Envelope records (2026-11-02)

1. The fix is central in `django-core-micha` for all apps (not cockpit-only).
2. The client address is `CF-Connecting-IP`; without it, the connection's own address (`REMOTE_ADDR`). `X-Forwarded-For` is never used for identity. `X-Real-IP` is not used for rate limiting either.
3. One shared resolver for the full address; DRF's throttle identity and allauth's rate limits both use it. The audit log keeps its own /24 truncation on top of the same resolution.

## Scope

1. A shared client-address resolver per decision 2, reused by the existing audit helper (behaviour of the audit log unchanged apart from no longer consulting `X-Real-IP`, see Risks).
2. DRF: the throttle identity of anonymous and webhook throttles in the shared settings/base classes uses the resolver.
3. allauth: its rate limits use the same client address (through allauth's own client-IP setting or hook in the installed version).
4. Release note naming the behaviour change for consumers.

## Non-goals

- No change to rate values, to key-based throttles (they identify by key, `CKP-OBS-46`), or to any app's own code.
- No consumer pin bumps in this order; consumers take the release through their normal core bump.

## Risks

- Trusting `CF-Connecting-IP` is safe only where an app is reachable through Cloudflare alone. If an origin accepts traffic that bypasses Cloudflare, a client could set the header itself. Before landing, the Orchestrator establishes (read-only, from outside) that the app origins do not answer requests that bypass Cloudflare, and records the evidence; a reachable origin stops the order.
- The audit log stops consulting `X-Real-IP`. No deployment of the estate sets it today as far as known; the Orchestrator confirms before landing.

## Required tests to write

- A request with a forged `X-Forwarded-For` and a `CF-Connecting-IP` is throttled under the `CF-Connecting-IP` address; changing only `X-Forwarded-For` does not reset its budget.
- Without `CF-Connecting-IP` the identity is `REMOTE_ADDR`, regardless of `X-Forwarded-For` and `X-Real-IP`.
- allauth's rate limit sees the same address as DRF for the same request.
- The audit helper still truncates to /24 and resolves through the shared resolver.

# B. Implementation map — addressed to the implementer

_Placeholder — filled by the Orchestrator on pickup._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**

_Placeholder — filled by the Orchestrator on pickup._
