# DCM-NOTIF-32 — Optional Web Push actions and recipient-scoped completion

## A. Envelope

**Tier:** 3 (shared notifications, authenticated mutation and token lifecycle). **Target:** `django-core-micha`, `main`. **Companion:** `UCM-NOTIF-18`. **First consumer:** Gustav (`GUS-NOTIF-5`, proposed in `GUS-NOTIF-6`).

### Goal and existing capabilities

A registered notification type can opt in to richer browser pushes. A recipient can mark the notification done without opening a window. DCM already owns typed notifications, localized per-recipient push rendering, the push delivery record, `NotificationRecipient.done_at` and canonical feed status updates. UCM owns the worker shipped to app origins. Both capabilities are listed in `webapp-management/SHARED_CAPABILITIES.md`.

### Scope and acceptance

- **Push options.** Notification types can supply optional `icon`, `badge`, `tag`, `renotify` and the action labels for `open` and `done`. Labels are resolved in the recipient's language using existing notification localization. A trusted app call can supply a tag per notification, so repetitions for the same record replace each other on supporting clients. `renotify` requires a nonempty tag. An optional destination for `open` follows the same-origin rule, with the notification URL as its default. No extras means the existing `title`, `body`, `url` payload. The preview preference and other delivery channels retain their existing behaviour.
- **Wire contract.** Keep the three existing keys. Send optional `icon`, `badge`, `tag`, `renotify` and `actions`. Action entries have `action` (`open` or `done`) and localized `title`. Only `open` can have an optional `url`. A push offered with `done` carries an opaque `done_token`. Keep the serialized UTF-8 payload within the Web Push plaintext budget, including options and token. Drop the extras when they would prevent delivery, keeping a usable base notification. Never emit a `done` button without a usable token.
- **CSRF contract.** `GET /api/notifications/push/csrf/` requires a valid application session and returns `200` with `{ "csrf_token": "…" }` containing a masked Django CSRF token. Let Django maintain its normal CSRF cookie or session state. Return `Cache-Control: no-store` and allow no cross-origin access, including from other origins trusted elsewhere by the app. This route returns no notification data and requires no push action token. Keep the existing `/api/csrf/` contract.
- **Authorization.** `POST /api/notifications/push/done/` accepts JSON `{ "token": "…" }` with the Django CSRF token in `X-CSRFToken`. Require a valid application session for the token's recipient, an `Origin` header equal to the configured `PUBLIC_ORIGIN` (scheme, host and port; `CSRF_TRUSTED_ORIGINS` does not widen this), and normal Django CSRF validation. The push action token is bound to that recipient, one push delivery and this single action, expires after 14 days and can be consumed once. A logged-out browser, stale session, different signed-in account, cross-origin request, missing or invalid CSRF proof, invalid or expired action token and replay make no changes. Return a generic denial without identifying the recipient, record or rejection cause. Keep all existing CSRF and authorization boundaries for other paths.
- **Effect.** A successful request atomically consumes the token and marks the associated `NotificationRecipient` done using the same state transition as the feed action. Emit the existing status update after a committed change. An already-done recipient can complete with a still-valid token without a second status change or event. Success returns HTTP 204. No other recipient, delivery status or business entity is changed. A concurrent click, including from another device displaying the same delivery, cannot use the token twice.
- **Credential handling.** Keep tokens out of URLs, logs, telemetry and feed/realtime serializers. Persistent server-side action-token state is a new additive model with a migration (next free number after `0011`). It stores only a SHA-256 hash of a random token with at least 32 bytes of entropy, the delivery, the recipient, the action, `expires_at` and `consumed_at`; the cleartext token exists only in the push payload. Dispatch a push carrying an action token only after the matching token and delivery have committed. Discard the pending send if the issuance transaction rolls back. Expired and consumed records follow bounded retention. Document the shared wire contract and ship an additive DCM release.

### Exclusions and risks

Gustav owns its image assets, per-event labels and tag selection. External-action handlers, new app actions, offline execution, logout behaviour changes and broader session or CSRF changes are out of scope. Leaked tokens, a stale notification clicked after account change, and provider rejection of oversized messages are the main risks. A browser may lack some display options, so feed usability cannot depend on them.

### Required tests to write

- Assert unchanged payloads when options are absent. Cover recipient languages, `open` destination, per-notification tags, invalid options, UTF-8 size fallback, preview preferences and intact existing channels. Verify secrets are absent from feed, realtime and error/log paths.
- Exercise the two requests using a real session cookie and enforced CSRF checks. Cover the masked CSRF response and no-store policy, missing and invalid CSRF headers, a foreign origin including one trusted for other app requests, an absent or stale session and a different signed-in recipient. Exercise expiry, replay, an already-done record and two simultaneous clicks. Verify rejection for the wrong action or an unrelated route, assert generic authorization denials, and keep existing feed CSRF checks green.
- Verify that an immediately clicked push can resolve a committed token and delivery. Roll back issuance and assert that the push sender was never called. Separately roll back the status write and verify that the action token remains usable.
- Confirm one recipient status change and one post-commit inbox update. Exercise notification preference and feed regressions and the affected permission/security inventories.

## B. Implementation map

- `src/django_core_micha/notifications/types.py`, `src/django_core_micha/notifications/api.py`, `src/django_core_micha/notifications/text_registry.py` and `src/django_core_micha/notifications/validators.py` cover typed notifications, app emission and localized text contracts.
- `src/django_core_micha/notifications/dispatch.py` and `src/django_core_micha/notifications/delivery.py` own per-recipient rendering, preview preferences and Web Push delivery. `api.py` connects immediate dispatch to the delivery record.
- `src/django_core_micha/notifications/models.py`, `src/django_core_micha/notifications/migrations/` and `src/django_core_micha/notifications/management/commands/prune_notifications.py` cover deliveries, recipient state and retention.
- `src/django_core_micha/notifications/views.py`, `src/django_core_micha/notifications/urls.py` and `src/django_core_micha/notifications/serializers.py` cover the canonical authenticated feed. `src/django_core_micha/api_urls.py` mounts its standard prefix.
- `src/django_core_micha/notifications/consumers.py` and `src/django_core_micha/notifications/delivery.py` cover the `notification.status` update transport.
- `src/django_core_micha/notifications/tests/` and `tests/test_security_notifications.py` cover API, recipient localization, dispatch, privacy and the affected permission boundary.
- `docs/design/notifications-platform.md`, `CHANGELOG.md` and `pyproject.toml` cover the notification contract and release.
- Environment: same-origin application service worker with session cookies, VAPID Web Push, synthetic users and a transaction-capable database for concurrency evidence.

## Preamble

> **You are the dispatched implementer, not the Orchestrator. Never delegate, dispatch, review,
> run `codex` or `codex exec`, write to git or edit the register or `.claude/codex-status.md`.**
>
> **Your first tool action is the exact command `codex-hook-canary-ws-gov-60`. Expected denial:
> `WS-GOV-60 canary: workspace hooks are active`. Otherwise report unverified hook coverage once
> and continue with the existing gates.**
>
> Read the nearest `AGENTS.md`, relevant role skill and `MEMORY.md` for conventions. This work
> order is the complete specification. Edit the scoped source and tests. Run only the new tests
> you wrote. The Orchestrator runs the authoritative evidence and independent reviews. Do not
> install dependencies, touch lockfiles, update memory, edit `WORK_ORDERS.md` or write to git. Use
> cohesive files named by behaviour. If a test cannot run in the sandbox, report `TESTS NOT RUN:
> <reason>`. Start with `PLAN:`, report progress and end with `RESULT: DONE` or
> `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

> **Implementer: stop here.** The Orchestrator owns dispatch, review, evidence and release.

- Follow `orchestrate-codex` and the Tier 3 review and security rules. Verify both new endpoints against the affected permission inventory and the existing feed auth and CSRF boundaries.
- Verify the companion `UCM-NOTIF-18` worker completes a synthetic push with a real authenticated session, normal CSRF enforcement and no open app window. Cover both a successful click and a failed click after an account switch. Confirm compatibility with the prior worker. Record the release consumed by `GUS-NOTIF-5`.

## Gate record

The operator confirmed the session, expiry and failure-handling requirements in the Gustav Notifications chat on 2026-11-25.

The operator requested integration of the review findings on 2026-11-25. The masked CSRF token route and the revised Envelope await Gate #1 for implementation.

Gate #1 approved by the operator 2026-11-28 (in chat, after the origin source `PUBLIC_ORIGIN` and the hashed token model with its migration were added to Part A).

## Completion

Implemented by codex/gpt-5.6-terra (medium), release 3.47.0, migration `0012_notificationpushactiontoken`. Fix round by the same route; the endpoint tests, the dispatch regression and the test stack were corrected by the Orchestrator because the sandbox cannot reach the database.

**Review:** two rounds, lenses envelope, regression, duplication, tests, sec_review on codex/gpt-6.1-terra and gpt-6-luna. Round 1 raised 7 findings (all accepted, fixed): payload size vs. serialization and escaping, `/\evil.example` open-redirect, renotify tag only at type level, session-only authentication, recipient lock for parallel tokens, token pruning, test coverage. Round 2 (closing) raised 7 (all accepted): one regression (oversize title aborting the caller, fixed in `_send_push`) and six test gaps (fixed as a post-review test delta). `sec_review` returned no finding in round 2. Carried: 1 (feed serializer has no explicit test that no token field exists; realtime events and logs are asserted).

**Tests:** `tests.py` backend gate 2226/2226 green (full scope once, then the notifications directory after the last fix; the earlier full run saw a tree change). Test-management scopes: `test_real_disposal_preserves_other_instance` red (leftover Docker volume `dcm-synthetic-tm-34406452...`, owner not verifiable, disposal script skipped it) and one parametrized launcher id reported missing although all 91 ran: pre-existing red, open, register row added.

**Own pass against Part A:** done as asked; nothing beyond Part A (the `critical=True` in two test types is a test-fixture choice). Origin is `PUBLIC_ORIGIN` exactly; token is SHA-256 hashed, 14 days, one use; the done transition equals the feed action (`done_at` plus `notification.status` after commit).

**Not verified (Part C):** the synthetic push with a real browser/worker against a running DCM stack was not run; the worker's exact headers are covered on both sides by unit tests (UCM worker test, DCM client tests with real session cookie and enforced CSRF). Status stays `in-review` until that run or an operator exception.

Part B: accurate.
