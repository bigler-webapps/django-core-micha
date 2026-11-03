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

- Trusting `CF-Connecting-IP` is fully safe only where an app is reachable through Cloudflare alone. **Measured 2026-11-03 by the Orchestrator:** the main-prod origin answers requests that bypass Cloudflare (`--resolve` to 91.99.199.135 → 200; webapp-management S260, fix WM-SEC-9 live on staging only). **Operator decision 2026-11-03: proceed anyway.** A client that reaches the origin directly can already set `X-Forwarded-For` freely today, so it gains nothing new by setting `CF-Connecting-IP`; for all traffic through Cloudflare the identity becomes unforgeable. The remaining gap is tracked as S260 and closes with the WM-SEC-9 rollout to the prod hosts; it is a residual risk, not a stop condition.
- The audit log stops consulting `X-Real-IP`. No deployment of the estate sets it today as far as known; the Orchestrator confirms before landing.

## Required tests to write

- A request with a forged `X-Forwarded-For` and a `CF-Connecting-IP` is throttled under the `CF-Connecting-IP` address; changing only `X-Forwarded-For` does not reset its budget.
- Without `CF-Connecting-IP` the identity is `REMOTE_ADDR`, regardless of `X-Forwarded-For` and `X-Real-IP`.
- allauth's rate limit sees the same address as DRF for the same request.
- The audit helper still truncates to /24 and resolves through the shared resolver.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly from scratch; open only the named files to verify. If you must dig deeper, delegate to a read-only Explore sub-agent (Haiku).

## Target working directory (absolute)

`C:/Users/Micha Bigler/Documents/webapps/django-core-micha` — NEVER the workspace root. Foreign uncommitted edits exist in `src/django_core_micha/scripts/sync_secrets.py` and `tests/test_sync_secrets.py`: do not touch, stage or revert them. Use explicit UTF-8 for all file I/O.

## Context package

- **New module `src/django_core_micha/auth/client_ip.py`** — the one shared resolver. `resolve_client_ip(request) -> str | None`: take `request.META["HTTP_CF_CONNECTING_IP"]` (stripped); if absent or not a valid IP (`ipaddress.ip_address`), use `request.META["REMOTE_ADDR"]` (same validation); else `None`. Return the normalised full address (`str(ip_address(raw))`), never truncated. It must never read `HTTP_X_FORWARDED_FOR` or `HTTP_X_REAL_IP`. Works on a Django `HttpRequest` and a DRF `Request` (DRF proxies `.META`). Also define `ClientAddressThrottleMixin` there with `get_ident(self, request)` returning `resolve_client_ip(request) or "unknown"` (DRF's `SimpleRateThrottle.get_ident` is the identity for anon/scoped/user-anonymous buckets).
- **`src/django_core_micha/auth/throttles.py`** (existing `_BodyKeyedScopedRateThrottle` and its subclasses stay as they are): add `CoreAnonRateThrottle(ClientAddressThrottleMixin, AnonRateThrottle)`, `CoreUserRateThrottle(ClientAddressThrottleMixin, UserRateThrottle)`, `CoreScopedRateThrottle(ClientAddressThrottleMixin, ScopedRateThrottle)` (DRF bases from `rest_framework.throttling`). Keep the mixin first in the MRO.
- **`src/django_core_micha/settings/settings_base.py:335-338`** `DEFAULT_THROTTLE_CLASSES`: point both entries at the new `CoreAnonRateThrottle` / `CoreUserRateThrottle` dotted paths. Rates untouched.
- **Throttle call sites** that name DRF's classes directly and must use the core classes instead: `src/django_core_micha/auth/views.py:20` (import) with uses at `:752` and `:886` (`throttle_classes=[ScopedRateThrottle, AnonRateThrottle]`); `src/django_core_micha/invitations/views.py:7` (import) with use at `:132`. `PerEmail*`/`PerAccessCode*` throttles are keyed by body, not address: unchanged.
- **`src/django_core_micha/auth/adapters.py`** `CoreAccountAdapter` (class at line 36): override allauth's `get_client_ip(self, request)` (the hook `allauth.core.internal.ratelimit` calls for `/ip` limits) to return `resolve_client_ip(request)`, raising `django.core.exceptions.PermissionDenied("Unable to determine client IP address")` when it is `None`, as the allauth default does. Do NOT set `ALLAUTH_TRUSTED_CLIENT_IP_HEADER` (it would return `None` where the header is absent instead of `REMOTE_ADDR`).
- **`src/django_core_micha/auth/_audit_helpers.py:6-30`** `_client_ip`: keep the `/24` (IPv4) and `/48` (IPv6) truncation and the `None` result for an unusable address, but obtain the address from `resolve_client_ip`; delete the `X-Real-IP` branch and fix the comment block to match.
- **Pitfalls:** `tests/settings.py` ships a minimal Django settings module (see the `tests/test_auth_throttles.py` docstring) — build requests with `django.test.RequestFactory`, wrap with `rest_framework.request.Request` where needed, and use the locmem cache. A DRF `Request.META` is the underlying request's `META`. Do not change `ACCOUNT_RATE_LIMITS_DEFAULTS`, any rate value, any consumer app, `pyproject.toml`, `CHANGELOG.md` or the version: the Orchestrator does those.

## Tests to write

New file `tests/test_client_ip.py`; update an existing test only if it asserts the removed `X-Real-IP` behaviour (grep `tests/` for `_client_ip` / `X_REAL_IP`).

1. Resolver: `CF-Connecting-IP` wins over `REMOTE_ADDR`; without it `REMOTE_ADDR`; an invalid `CF-Connecting-IP` falls back to `REMOTE_ADDR`; IPv6 is normalised; `X-Forwarded-For` and `X-Real-IP` never influence the result (parametrised, with and without the CF header).
2. DRF: with `CoreAnonRateThrottle` (small rate such as `2/min` via a subclass `THROTTLE_RATES` or a settings override), a request carrying a forged, rotating `X-Forwarded-For` plus a fixed `CF-Connecting-IP` is throttled after the budget, and changing only `X-Forwarded-For` does not reset it; without the CF header the bucket is `REMOTE_ADDR` regardless of the forged headers. Add at least one assertion that `CoreScopedRateThrottle` and `CoreUserRateThrottle` use the same `get_ident`.
3. allauth: for the same request, `CoreAccountAdapter().get_client_ip(request)` equals the DRF throttle's `get_ident(request)`; with no resolvable address it raises `PermissionDenied`.
4. Audit helper: `_client_ip` returns `a.b.c.0/24` for IPv4 and a `/48` network for IPv6, resolves through the CF header, ignores `X-Real-IP` and `X-Forwarded-For`, and returns `None` for an empty or invalid address.

## Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests the `Tests to write` section calls for AND **RUN the tests you just wrote** to confirm they execute and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the authoritative set + does the independent review after you finish — those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final `RESULT: DONE|BLOCKED <reason>`.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line. Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no reviewers, no verification run, no register edit, no commit.** You ARE the implementation instance described below; do NOT launch or dispatch another implementer.

## Execution directive

Configured `implementation` entry (`codex/gpt-6-luna`), CLI route from the dcm repo, WO passed on stdin. No `codex-status.md` line for 2026-11-03: Codex is used.

Launch evidence: pending.

## Review routing

Tier 3: the four `review` lenses concurrently plus `sec_reviewer`, then one re-run on the corrected diff. The Orchestrator reads the diff against Part A during the batch and does its own security pass.

## Verification

Scoped tests: `tests/test_client_ip.py`, `tests/test_auth_throttles.py`, `tests/test_account_rate_limits.py`, the audit tests touching `_client_ip`, `tests/test_bootstrap.py`, `tests/test_invite_link_lifetime.py`. X-Real-IP check (2026-11-03): the only reader in the workspace is Cinevia `backend/backend/views.py` (app code, out of scope); no deployment config sets the header; the dcm audit helper stops reading it. Origin evidence: see Risks.

## Register + commit

Row to `done` with the review line and landing SHAs; minor release 3.20.0 (version bump, CHANGELOG naming the behaviour change) per the repo's publish flow, as a separate commit.
