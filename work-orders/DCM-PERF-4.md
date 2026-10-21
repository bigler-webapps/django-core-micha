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

Placeholder. Not yet mapped: context package, target directory and preamble are missing, so this
WO must not be dispatched in its current state.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**
> Everything below describes what the Orchestrator does AFTER you finish.

To be filled by the Orchestrator. Recorded from the requirements round (2026-10-21): acceptance is
measured before/after (request count and serial levels at page start) once hram has adopted it in
`HRAM-WEBPERF-4`, recorded in the register Notiz; no fixed budget.
