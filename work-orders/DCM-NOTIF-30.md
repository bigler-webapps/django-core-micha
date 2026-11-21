# DCM-NOTIF-30 — Notification delivery, inbox and policy contracts

**Target** `django-core-micha` on `main`

**Tier** 3 for shared notification behaviour, recipient scoping and stored delivery state.

**Status** Prepared for Gate #1 with the UCM-NOTIF-16 and JG-NOTIF-29 integration contract.

## A. Envelope

### Goal

Publish one compatible DCM release for the outstanding shared backend work in `jg-ferien` JG-FIX-21 and the notification standards and settings in [JG-NOTIF-29](../../jg-ferien/work-orders/JG-NOTIF-29.md). [UCM-NOTIF-16](../../ui-core-micha/work-orders/UCM-NOTIF-16.md) consumes the inbox and preference contracts in the same adoption window.

The role replacement, waitlist confirmation and live Worship notice label were already changed in `jg-ferien` at `26c862a9`. `jg-ferien` owns its event and organisation policies, survey audience selection, task deep links, and its digest integration. This package owns the shared APIs and delivery semantics those changes need.

### 1. Return the real email outcome and recover definite failures

The shared email helper logs send exceptions and returns normally. The canonical dispatcher currently reports success unconditionally after this call. The app digest can then mark a failed send as sent. Pending delivery rows prevent re-claiming a threshold on later jobs.

- Expose a machine-readable per-recipient result through the shared transport and dispatcher. Distinguish accepted by the mail backend, definitive failure, not attempted or ineligible, and uncertain acceptance. A backend count of zero is not acceptance. Preserve recipient-by-recipient isolation, language fallback, rendering and preference checks. Successful backend acceptance is the application's recorded success boundary.
- Provide shared claim and result handling usable by the app digest. An accepted reminder threshold stays complete. A definitive failure may be retried by a later eligible run. A claim that already has an active worker must not be sent concurrently. Distinguish a claim abandoned before any transport attempt from one interrupted during an attempt. Record uncertain acceptance for reconciliation and avoid an automatic repeat where acceptance cannot be ruled out. Recheck eligibility before dispatch, including current opt-in.
- The canonical chat email path records the real result using the same transport seam. Browser-push, recipient policy, language routing and transient preview handling retain their current contracts. Preserve historical `sent` rows and the same threshold schedule. Provide compatibility behaviour for existing callers that do not consume the result.

### 2. Page the canonical inbox and mark all eligible rows as read

Publish the contract for UCM-NOTIF-16. Preserve existing single-row and selected-ID marking, default first-page behaviour, and response fields for existing consumers.

- An authenticated client can continue the canonical feed from the first page. Give equal timestamps a deterministic tie-breaker. The contract defines continuation, an exhausted page and how newer arrivals interact with traversal. The client can de-duplicate by stable recipient identity. Existing pagination consumers retain their supported requests.
- Add an account-scoped bulk-seen action covering the unread inbox, including rows outside loaded pages. It uses a server-defined boundary at the action. An eligible row arriving after that boundary remains unread. The operation is idempotent and returns a result the client can use to reconcile its badge and loaded seen state. Define how a second client is refreshed with existing real-time mechanisms or a compatible change.
- Use consistent eligibility in canonical feed, unread count and bulk marking. Include eligible derived tasks and exclude feed-hidden types and inactive or dismissed tasks from actionable unread totals. Marking an entry as seen changes only its seen state. Test a second account and both the ID-based and bulk operations under authentication. Prefer a bounded bulk update over one request or WebSocket event per recipient row.

### 3. Expose personal category and channel preferences

The shared category-by-channel model and precedence rules already exist. Add an API surface for the authenticated account to read, set and reset explicitly supported pairs, preserving the existing general preferences and push-subscription endpoints.

- The app can declare which pairs it offers. For `jg-ferien` the consumed contract covers messaging email, messaging browser push and task digest email. Return the supported labels or stable translation keys, the explicit choice `inherit`, `enabled` or `disabled`, and the effective preference. Technical push availability stays distinct from the preference.
- Setting one pair affects only that pair. Reset removes the explicit setting so the existing general or built-in fallback applies again. Validate ownership, category, channel and value on the server. A pair's setting does not grant eligibility or change the recipient set. Category subscriptions remain a separate contract.
- Consumers that register zero editable pairs retain their current global settings and channel behaviour. `jg-ferien` connects its digest to the same resolver. Core defines a compatible resolver contract for digest delivery without reinterpreting critical or transactional mail settings.

### Shared wire contract for UCM and app adoption

| Operation | Contract |
| --- | --- |
| Continue the inbox | `GET /api/notifications/feed/` retains `results`, `next`, `previous` and `count`. A continuation comes from `next`, is used only on the configured application origin, and ends with `next: null`. Preserve legacy page requests. Order by notification creation time with a stable recipient-ID tie-breaker. Use `results[].id` to merge rows. New feed responses advertise `capabilities.mark_all_seen: true`. |
| Mark the inbox as seen | `POST /api/notifications/feed/mark-all-seen/` uses an empty request object and the authenticated account. It returns `updated` and `unread_count`. Resolve the eligible set at the action's boundary. A row outside that boundary retains its own `seen` state. The existing selected-ID operation stays available at `/api/notifications/feed/mark/`. |
| Revise a live inbox | Publish one `notification.inbox_changed` message in the existing notifications envelope after a successful bulk mutation, scoped to the current user's channel. The message invalidates feed and unread count. Consumers refresh the affected state. Existing per-item events and payloads retain their meaning. |
| Read editable channel pairs | `GET /api/notifications/preferences/channel-matrix/` returns `{results: [...]}`. Each row carries `category`, `channel`, `label_key`, `label` as translated fallback, `choice` and `effective_enabled`. The three app pairs are `(messaging, email)`, `(messaging, push)` and `(todo, email)`. Apps declare supported pairs. An empty list is valid. |
| Update or reset a channel pair | `PATCH /api/notifications/preferences/channel-matrix/` accepts exactly `category`, `channel` and `choice`, with `choice` one of `inherit`, `enabled`, or `disabled`. It returns the authoritative updated row in the same shape. `inherit` clears the explicit pair preference. Unsupported input returns a validation error. The request identity determines ownership. |

An older backend can omit the bulk capability or lack the channel-matrix route. UCM uses the published feature signal or treats a missing matrix route as the legacy case. Actual load and validation errors remain visible to the user. General channel and browser subscription routes retain their existing contracts. `effective_enabled` is the preference decision. The UI combines it with the device's current browser subscription state.

### 4. Supply a shared task policy and state seam for the app

- Canonical task derivation must accept an app-resolved, per-seed effective enabled state and lead time, or provide an equivalent documented hook. This lets `jg-ferien` apply its own event exception, organisation standard and app default consistently in the bell, badge and digest. The shared code consumes resolved values and keeps the existing single-scope fallback for providers without the new policy data.
- The app must be able to maintain independent status and delivery history for each survey, including an existing survey's dismissal through a temporary provider outage. Support a stable canonical identity through the published provider contract. Use an existing notifiable identity if the app can provide one cleanly. Any new shared identity contract is generic and opt-in. Stale-overlay reconciliation must support retaining state for provider occurrences that can legitimately return. Keep the existing default lifecycle for unaffected providers.
- A settings change can suppress or defer a reminder without erasing its completion, dismissal, or successful threshold history. The app supplies its eligibility decisions and preserves legacy rows whose survey or event identity can be established. DCM supplies stable persistence semantics and bounded lookups. Date rendering and task content are owned by their existing layers.

### Scope, compatibility and release

Use additive contracts wherever possible, with migration coverage for any required persisted delivery state. Preserve existing public route and preference defaults for consumers that adopt neither new surface. Keep delivery retries and history scoped to the relevant recipient and task occurrence. Historical backfill sends, production scheduler changes, app-wide changes to recipient policy, generic jobs infrastructure and unrelated auth or notification changes are separate work.

Deliver one DCM release with one changelog entry describing the contracts, their compatibility and migration implications. Coordinate UCM-NOTIF-16 against this contract. JG-FIX-21 and JG-NOTIF-29 use the same verified DCM/UCM release pair in one app pin adoption. Record the actual versions after checking the release line and changelog.

### Required tests to write

| ID | Expected result |
| --- | --- |
| DCM-MAIL-1 | A real dispatcher call against a fake transport reports accepted, zero accepted, definite failure, uncertainty and not attempted accurately. Another recipient's send continues. Existing rendering, language and opt-out behaviour remains valid. |
| DCM-MAIL-2 | In the shared digest claim flow, concurrent invocations cannot claim one threshold twice. A definite failure can succeed on the next eligible run. A completed threshold stays complete. Interrupted and uncertain attempts follow their documented recovery branch. An opt-out before retry prevents delivery. |
| DCM-FEED-1 | More than 20 visible rows, equal timestamps, active tasks, hidden and dismissed rows. Traverse the canonical feed, assert deterministic order and stable identity, and preserve legacy first-page requests. |
| DCM-FEED-2 | Mark all with multiple pages, a different user's rows and a new arrival across the boundary. Verify ownership, unread count after refresh, idempotency and seen-only side effects. Keep individual marking tests. |
| DCM-PREF-1 | Test declaration, no declared pairs, inheriting/setting/resetting one pair, independence across categories and fallback changes. Reject unknown pairs and other-account writes. Assert actual router and digest preference decisions. |
| DCM-TODO-1 | Test default provider compatibility, two scopes with different resolved values, explicit zero and disabled values, direct/feed/count/digest parity, and bounded policy lookups. |
| DCM-TODO-2 | Two distinct provider occurrences retain independent seen, dismissed and delivered state. Temporary absence and settings suppression preserve state for the opted-in lifecycle. Existing provider cleanup still passes. Verify supported old records through any migration. |

## B. Implementation map

Working directory `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

Source baseline is `main` at `775af17`. The contract in Part A is shared with UCM-NOTIF-16 and JG-NOTIF-29. All production edits in this WO belong to DCM. App-specific candidate selection, organisation settings and survey audience rules are consumer inputs.

| Area | Named seams and existing tests |
| --- | --- |
| Transport and dispatch | `src/django_core_micha/notifications/delivery.py:108`, `dispatch.py:119,198`, `api.py:87` and `models.py` for existing delivery state. Extend `tests/test_delivery.py`, `test_dispatch.py`, `test_canonical_notification_api.py` and the language regression tests under the notifications module. |
| Claims and todo policy | `src/django_core_micha/notifications/todo/digests.py:40,55,92`, `todo/registry.py`, `todo/service.py:41` and the notification/recipient models. Extend `tests/test_todo_digests.py`, `test_todo_service.py` and `test_todo_registry.py`, with database concurrency coverage on a production-shaped backend where needed. |
| Inbox | `src/django_core_micha/notifications/views.py:160,212,230`, `serializers.py` and `urls.py`. Start from `tests/test_canonical_notification_api.py`, `test_notification_feed_indexes.py` and the notification WebSocket inventory. Keep request/response and feature-detection evidence in the released contract. |
| Preferences | `src/django_core_micha/notifications/models.py:66`, `prefs.py:9`, `views.py:38`, `serializers.py`, `router.py` and `urls.py`. Extend `tests/test_preference_matrix.py`, `test_notif26_reach_and_subscriptions.py`, and push subscription and ownership cases. |

Consumer context for read-only alignment is `jg-ferien/backend/events/{todo_channel,todo_canonical,survey_client}.py`, particularly its app-patched digest and both task derivation paths. UCM-NOTIF-16 names the shared UI contract consumer. Use the core repository's own test project for fixtures and settings. Record the shared API in its existing documentation and changelog.

### Implementation sequence and contract checks

1. **Immediate delivery result.** Follow `_send_email` in `delivery.py` through `EmailDispatcher.deliver` and `dispatch` in `dispatch.py`, then into `api.notify`. Today `notify` creates an immediate delivery row with a null threshold, calls `dispatch`, and updates status using its `True`/`False` result. Make the richer transport outcome reachable at this boundary and in the app digest. Preserve the public `notify()` return value and compatible behaviour for existing callers and channels. Keep the email renderer and recipient language selection in `dispatch.py`; the provider result comes from the per-recipient send call in `delivery.py`. `src/django_core_micha/messaging/notifications.py` is the existing chat consumer and a regression input.
2. **Digest claims, retries and lifecycle.** The current `_get_digest_delivery_with_retry` in `todo/digests.py:55` does `get_or_create` on recipient, email channel and threshold. An existing failed row is not a new claim. Define the shared claim and state transitions around `NotificationDelivery` (`models.py:287`), preserving the separate uniqueness constraints for immediate and threshold deliveries. A retry must acquire exclusive ownership before transport, and its write-back must apply to the current claim. Eligibility or preference exclusion, accepted transport, definite failure, and possible acceptance have separate outcomes for callers. Keep interrupted pre-attempt and in-flight cases distinct. Add a migration under `src/django_core_micha/notifications/migrations/` only where the approved state requires new storage. Existing `sent` evidence stays authoritative for deduplication.
3. **Policy and canonical task identity.** `TodoSeed` and `TodoTypeConfig` in `todo/registry.py:8,21` feed `_materialized_candidates` in `todo/service.py:41`; it currently resolves one scope and `TodoOverride`. Extend that seam for the opt-in effective policy and stable occurrence or notifiable identity in Part A. Both derivation and count must see the same candidates. Cover direct, digest and feed callers through their existing public or app-facing seams. `_reconcile_todo_overlays` in `todo/digests.py:92` currently treats disappearance from one run as stale state. Apply the opted-in retention policy there so temporary suppression does not drop persisted dismissal, completion or delivery history. A provider without new policy data continues to resolve its current configuration and lifecycle.
4. **Feed and seen boundary.** `CanonicalInboxView`, `CanonicalUnreadCountView` and `CanonicalMarkView` in `views.py:164,212,230` are the existing contracts. Keep the feed's persisted event rows and derived task rows aligned with the actionable unread set. Use the existing recipient identities and notification envelope for the single account-scoped invalidation after a committed bulk update. Add the advertised capability and scoped bulk endpoint in `urls.py` and `serializers.py` as required by Part A. Verify deterministic continuation when timestamps tie. The inbox update targets seen state only, so eligibility and done/dismissed state cannot be changed by the bulk request.
5. **Self-service channel matrix.** Add the declared-pair API to `views.py`, `serializers.py` and `urls.py` using `NotificationCategoryChannelPreference` and `is_channel_enabled` in `models.py:66` and `prefs.py:9`. That resolver already checks the per-category override, channel default, legacy opt-in and built-in default in that order. Preserve that order and keep channel preferences separate from category subscriptions. Expose the declared pairs and their effective values without requiring UCM to reimplement the fallback. Use server-derived identity for the write and validate only declared pairs. `docs/design/notifications-platform.md` is the existing shared documentation target if the contract description needs updating; the Orchestrator owns release metadata and changelog.

### Test ownership and consumer handoff

| Criteria | Concrete test mapping |
| --- | --- |
| DCM-MAIL-1 | Extend `src/django_core_micha/notifications/tests/test_delivery.py`, `test_dispatch.py` and `test_canonical_notification_api.py`. Use `tests.settings` and a fake transport at the message send boundary. Check recorded chat email outcomes and preserve `test_recipient_language.py` coverage. |
| DCM-MAIL-2 | Extend `src/django_core_micha/notifications/tests/test_todo_digests.py`. Verify stored state and transport call count for accepted, retried, interrupted, opted-out and uncertain cases. Use a transactional database fixture for contested claims. Record backend limitations for concurrency tests so the Orchestrator can run the authoritative affected case on the requisite backend. |
| DCM-FEED-1, DCM-FEED-2 | Extend `src/django_core_micha/notifications/tests/test_canonical_notification_api.py` and `test_notification_feed_indexes.py`. Use the existing testapp provider fixture, two accounts, tied dates, enough rows to require continuation and an arrival across the bulk boundary. Verify channel envelope and eligibility alongside `test_ws_inventory.py`. |
| DCM-PREF-1 | Extend `src/django_core_micha/notifications/tests/test_preference_matrix.py` and `test_notif26_reach_and_subscriptions.py` for declared, empty and unsupported pairs. Test persisting and deleting only the current user's row, resolver decisions and unchanged push-subscription ownership. |
| DCM-TODO-1, DCM-TODO-2 | Extend `src/django_core_micha/notifications/tests/test_todo_registry.py`, `test_todo_service.py` and `test_todo_digests.py`. Share a synthetic time and fixture definition across feed/count/digest checks. Compare an opted-in provider's returning and distinct occurrences with the old provider lifecycle. Cover pre-change rows if storage changes. |

Before handing off, identify the exact provider and digest entry points that the app will adopt, their default behaviour for legacy callers, and the input/output examples from the implemented inbox and matrix tests that UCM can reuse. Use `python -m pytest` from the DCM root with the new or updated test node IDs, using the repository's `tests.settings`. Keep setup, publication, and the broader regression run with the Orchestrator.

### Implementer preamble

> You are the dispatched implementer for this core scope. The committed Parts A and B are the complete specification. Read the nearest instructions, role skill and memory for conventions. The first tool action is `codex-hook-canary-ws-gov-60`. Expect `WS-GOV-60 canary: workspace hooks are active`. Report unverified coverage once if absent.
>
> Edit assigned source and tests only and run only the tests you wrote once. The Orchestrator owns releases, pins, dependency management, review, wider tests, register and Git. Do not invoke `codex` or `codex exec`. Leave an uncommitted diff. Read focused ranges, use behaviour-based test names, emit `PLAN` and `PROGRESS` lines, and end with `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

> Implementer instructions end at Part B.

1. Record Gate #1 and pass the shared wire contract in Part A to the UCM and app implementers. Coordinate opt-in provider hooks with the app owner. The Orchestrator owns the final release version and changelog.
2. Use the configured implementer and independent Tier 3 envelope, regression, duplication, tests and security lenses. Check owner scoping, persisted history, migration and concurrency against the actual diff. Run the authoritative core tests, the affected security inventory and consumer compatibility checks. Use synthetic fixtures and a fake transport.
3. Land and verify the single package publication through the existing release workflow. Provide the published version, tests, migration guidance and contract samples to UCM-NOTIF-16 and the app WOs. Keep application pin adoption scoped to the paired verified versions and actual app tests.

## Completion

Landed as django-core-micha 3.41.0 (migration 0011).

Tests: notification suite 185 passed, 2 skipped (host pytest, SQLite).

Review: round 1 plus one closing round, five lenses (envelope, regression, duplication, tests, security). Round 1 findings were fixed in scope (digest body from still-eligible occurrences, claim-token guard, real email outcome persistence). The closing round raised no blocker and no security finding.

Carried to the register (not blockers):
- Digest still gates on the general email opt-in and does not re-evaluate the todo/email resolver or provider policy and lead time after claiming.
- Concurrency tests acquire claims sequentially (SQLite cannot run true concurrent writers); a PostgreSQL concurrent test is missing.
- Matrix endpoint is tested through the view, not the URL route; only one German label is asserted.
- Default feed returns `previous: null` (keyset cursor); backward navigation needs the page-number mode.
- `is_digest_email_enabled` overlaps `is_channel_enabled(user, "todo", "email")`.

Release evidence: published by the existing workflow; version recorded in the register.

## Gate record

On 2026-11-21 the operator requested preparation of the combined Core package alongside JG-NOTIF-29. Gate #1 approved by the operator 2026-11-21 (Core package, Tier 3, together with UCM-NOTIF-16 and JG-NOTIF-29).
