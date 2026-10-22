# DCM-PERF-4 — One bootstrap request for the data every page needs at start

# A. Envelope — authored by the Expertenchat

## Goal & expected outcome

- Goal: a consuming app can obtain, in a single request, everything its shell needs before the
  first page renders — the current user, the auth methods, the notification feed and unread count,
  and app-specific start data — instead of two serial rounds of separate requests.
- Expected outcome: dcm offers a public `GET` bootstrap endpoint whose response carries:
  - `user`: exactly what `users/current` returns for the same session, or `null` when anonymous;
  - `auth_methods`: exactly what `/api/auth-methods/` returns;
  - `notifications`: for an authenticated user, the feed exactly as `/api/notifications/feed/`
    returns it without parameters, and the count exactly as `/api/notifications/feed/unread-count/`
    returns it; `null` when anonymous;
  - `extensions`: an object keyed by provider name holding each app-registered provider's payload,
    for authenticated users only; empty when anonymous or when the app registers none.
  The response also ensures the CSRF cookie is set, so a first visit needs no separate CSRF request
  before it.
- Consuming apps can register named extension providers. A provider receives the request and
  returns a JSON-serialisable payload.

## Background (measured 2026-10-21 from Tanzania)

Every hram page starts with two serial API levels: `users/current` + `auth-methods` (ucm
`AuthContext`), then `notifications/feed` + `unread-count` (ucm `NotificationsProvider`) and hram's
own `application/access`. A first visit adds a CSRF request before both. From Tanzania each level
costs at least about 400 ms (120-200 ms to the Cloudflare edge in Johannesburg plus 200-275 ms to
the origin in Germany), often more on a jittery link. One bootstrap request removes at least one
whole level per page load.

## Scope + non-goals

- In scope: the endpoint, its URL registration in dcm's shared URL set, the extension-provider
  registration mechanism, and their tests.
- Decided in the requirements round (2026-10-21):
  - app extensions are supported (hram will register its application-access data);
  - the notification feed and unread count are part of the response;
  - adoption is opt-in per app on the ucm side (`UCM-PERF-4`); this WO changes nothing for apps
    that do not call the endpoint.
  - A failing extension provider is isolated: its key carries `null`, the error is logged, the rest
    of the response is unaffected.
- Non-goals / do not touch:
  - the existing endpoints (`users/current`, `auth-methods`, notification feed and count) keep
    their URLs, payloads and behaviour — other apps and later refreshes still use them;
  - no change to the notification WebSocket;
  - no change to authentication, session handling or permission logic; the endpoint reuses the
    same serializers and checks as the existing endpoints;
  - no frontend change (that is `UCM-PERF-4`).

## Tier · precondition / gate

- Tier 3: shared-core change and a new endpoint that returns user data on an auth surface.
- Precondition: none. `UCM-PERF-4` builds against this contract; hram adopts both in
  `HRAM-WEBPERF-4` after its pin bumps.

## Risks

- Exposure: the endpoint is reachable anonymously. For an anonymous request it must return nothing
  beyond what `/api/auth-methods/` already exposes publicly — no user data, no notifications, no
  extension payloads.
- Drift: if the bootstrap builds its parts differently from the existing endpoints, the two paths
  diverge over time. Each part must come from the same serializer or function the existing endpoint
  uses.
- An inactive user or a user in a partially authenticated state (for example pending MFA) must get
  the same answer for `user` that `users/current` gives today.
- Throttling: the endpoint must not become a way around throttles that protect the individual
  endpoints.
- Latency: the combined response must not be slower than the slowest of its parts by more than
  their sequential overhead; an unbounded query count would erase the gain.

## Required tests to WRITE (you write them and run YOUR OWN new ones; the Orchestrator's run is the gate)

- Anonymous request: `user` is `null`, `notifications` is `null`, `extensions` is empty,
  `auth_methods` equals the `/api/auth-methods/` response, and the CSRF cookie is set.
- Authenticated request: `user`, `auth_methods`, `notifications.feed` and
  `notifications.unread_count` equal the responses of the four existing endpoints for the same
  session (parity).
- Inactive and pending-MFA sessions: `user` matches what `users/current` returns for that state.
- Extension providers: a registered provider's payload appears under its key for an authenticated
  user; it is not called for an anonymous user; a provider that raises yields `null` for its key
  while the other keys are still returned.
- The query count of an authenticated bootstrap request stays within a fixed ceiling (no N+1).
- Existing endpoint tests stay green unchanged (no edits to their expectations).

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

## Target working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (git root, branch `main`). Use absolute
paths. The checkout is shared: `src/django_core_micha/scripts/sync_secrets.py` and
`tests/test_sync_secrets.py` carry another session's uncommitted edits — do not open, format or
revert them, and preserve any other file you did not create.

## The contract is fixed — implement it exactly

`docs/design/bootstrap.md` (whole file) is the public contract: keys, how each part is produced,
anonymous vs. authenticated shape, error statuses and bodies, headers, the provider registry.
Do not edit `docs/`, `CHANGELOG.md`, the version or `WORK_ORDERS.md`. If the contract is
contradictory or cannot be implemented as written, stop with `RESULT: BLOCKED <the contradiction>`.

## Context package

**Why parts are resolved by URL name (the parity seam).** `users/current` is not a dcm view: every
app subclasses `BaseUserViewSet` (`src/django_core_micha/auth/views.py:189`, action `current` at
`:336-347`) with its own serializer and registers it as `router.register(r"", UserViewSet,
basename="user")` under `api/users/` — all 18 consumers, measured 2026-10-22 — so the URL name
`user-current` reaches the app's own view. Apps may also replace a dcm endpoint under the same name:
spesix mounts `invitation_only_auth_methods_view` as `name="auth-methods"` before dcm's
`api_urls`. Resolving `reverse(<name>)` and then `resolve(<path>)` and calling that view is the
only way every part stays identical to its endpoint. Do not call `get_auth_methods()`, serializers
or querysets directly.

**The parts today**

- `auth-methods`: `auth_methods_view` (`auth/views.py:159-166`), plain Django view, public, no
  throttle.
- `canonical-notification-feed`: `CanonicalInboxView` (`notifications/views.py:160-209`), DRF
  `ListAPIView`, page size 20; its pagination links are built from the request URL, so the internal
  request must carry the feed's own path (contract: links point at the feed).
- `canonical-notification-unread-count`: `CanonicalUnreadCountView` (`notifications/views.py:212-227`),
  body `{"count": n}`.
- Routes: `src/django_core_micha/api_urls.py:28-79` (`auth-methods` at `:43`,
  `notifications/` include at `:75`; names in `notifications/urls.py`).
- DRF defaults (`settings/settings_base.py`, `REST_FRAMEWORK`): `SessionAuthentication`,
  `IsAuthenticated`, `AnonRateThrottle` 100/day and `UserRateThrottle` 10000/day. DRF checks
  permissions before throttles, so an anonymous `users/current` is refused (403) without spending
  anon quota — the reason the bootstrap must not be a DRF view with its own throttles and must not
  call DRF parts for anonymous requests.

**Files to create or change**

- New `src/django_core_micha/bootstrap.py`: the provider registry
  (`register_bootstrap_provider`, `unregister_bootstrap_provider` — the latter a test-cleanup
  helper — pattern: `src/django_core_micha/activity/policy.py:28-48`) and `bootstrap_view`, a plain
  Django view decorated for GET only, `ensure_csrf_cookie`, `Cache-Control: no-store` and
  `Vary: Cookie`. Internal requests: a copy of the incoming `HttpRequest` changed to `GET`, the
  part's path (`path`, `path_info`, `META["PATH_INFO"]`), an empty query string/`GET`, sharing
  session, user, cookies and headers; the original request must not be mutated. Read a DRF
  `Response`'s `.data`/`status_code`; for a plain `JsonResponse` decode its content. Logging on a
  provider failure: logger name and message with the provider name and exception class only.
- `src/django_core_micha/api_urls.py`: `path("bootstrap/", bootstrap_view, name="dcm-bootstrap")`.
- New `tests/test_bootstrap.py` and a test URLconf (for example `tests/urls_bootstrap.py`) that
  mounts `path("api/", include("django_core_micha.api_urls"))` and a users router with a
  `BaseUserViewSet` subclass registered with `basename="user"` (minimal serializer pattern:
  `tests/test_self_edit_delete_restriction.py:1-40`; `tests/settings.py` has no profile model).
  `tests/settings.py` has a minimal `MIDDLEWARE` (only `AccountMiddleware`) and no
  `REST_FRAMEWORK`: use `override_settings` per test module for `ROOT_URLCONF`, the session, CSRF,
  authentication and allauth middleware, and the DRF defaults the contract relies on
  (authentication, permission and throttle classes/rates as in `settings_base`), with the
  locmem cache cleared between tests so throttle state does not leak.

## Required tests (Part A) — how to make them pin the behaviour

- Parity: for the same logged-in Django test `Client`, `user`, `auth_methods`,
  `notifications.feed` and `notifications.unread_count` equal the JSON of the four separate
  endpoints (create a few canonical notifications for the user first, so the feed is not empty).
- Anonymous: exact anonymous shape, `auth_methods` equal to `/api/auth-methods/`, the CSRF cookie
  set on the response, and no provider called.
- Inactive account (logged in, then `is_active=False`) and a login waiting for its second factor
  (allauth's pending login state in the session, no logged-in user): `user` equals what
  `users/current` gives (401/403 → `null`, anonymous shape).
- An app override: a test URLconf that mounts its own view under `name="auth-methods"` before
  dcm's `api_urls` — the bootstrap returns the override's payload.
- Providers: payload under its key for an authenticated user; not called for anonymous; a raising
  provider and a provider returning a non-serialisable value each yield `null` while the other keys
  and providers are returned; duplicate-name registration raises; invalid names are rejected.
- Failure semantics: a part answering 429 (exhaust `UserRateThrottle` with a tiny rate) makes the
  bootstrap answer 429 with `{"detail": "bootstrap_part_failed", "part": ...}` and the part's
  `Retry-After`; a URLconf without `user-current` gives 501 `bootstrap_unavailable`; `POST` gives
  405; every 200 carries `Cache-Control: no-store`.
- Query count: an authenticated bootstrap with 3 and with 25 notifications runs the same number of
  queries (`CaptureQueriesContext`), and that number stays under a fixed ceiling you state in the
  test.
- Existing endpoint tests are not edited.

Run only the tests you wrote, from the target working directory:
`python -m pytest tests/test_bootstrap.py -q -p no:cacheprovider`

## Invariants and pitfalls

- No change to the four existing endpoints, their serializers, permissions or throttles, to
  authentication, sessions or the notification WebSocket.
- Anonymous responses expose nothing beyond `auth_methods`.
- Files are UTF-8: open them with explicit `encoding="utf-8"`.

## Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

Work from this package; do not explore broadly from scratch; open only the named files to verify.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

### Execution directive

Configured `implementation`: `codex/gpt-6-luna`, CLI route from the Claude Orchestrator
(`codex exec -C <target dir> --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -m
gpt-6-luna -c model_reasoning_effort=high -`, WO on stdin). Dispatched only after DCM-PERF-3 has
landed (operator order: PERF-3, then PERF-4). Launch evidence: (filled after dispatch).

### Review routing

Tier 3: all `review.lenses` plus `sec_review`, one concurrent batch, `codex exec --sandbox
read-only -m gpt-6-luna`, diff inline (contract doc included); `review_fallback` only if Codex is
unavailable. No `ui_review`.

### Verification

`tests/test_bootstrap.py`, the notification feed tests (`src/django_core_micha/notifications/tests`),
`tests/test_self_edit_delete_restriction.py`, `tests/test_auth_throttles.py`; release as a minor
(new public endpoint and registry).

Recorded from the requirements round (2026-10-21): Recorded from the requirements round (2026-10-21): acceptance is
measured before/after (request count and serial levels at page start) once hram has adopted it in
`HRAM-WEBPERF-4`, recorded in the register Notiz; no fixed budget.
