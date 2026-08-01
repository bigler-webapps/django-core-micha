# Changelog

## [2.39.1] — 2026-08-01

### Fixed

#### MSG-2g — deduplicate multi-participant conversation lists correctly

`GET /messaging/conversations/` now applies the requesting participant's active and archive-state
conditions through the same relation join. Multi-participant conversations therefore appear once,
rather than once per matching participant; `include_archived=true` continues to include the
requesting user's archived participation row.

## [2.39.0] — 2026-08-01

### Fixed

#### MSG-2f — group/broadcast conversation lifecycle: membership reconciliation + idempotency

`create_conversation()` previously always did a blind `Conversation.objects.create(...)` for `group`/
`broadcast`/`managed`, even though the schema already had a `(app,scope,kind,external_key)` unique
constraint for `managed`/`broadcast` — a second call for the same key raised `IntegrityError`. Widened
that constraint to cover `group` too and made keyed creation idempotent: an authorized actor
re-opening an existing keyed conversation now joins it (added as a participant, `participant_users`
processed, membership reconciled) rather than either crashing or being silently locked out. Membership
reconciliation (`reconcile_membership`, calling the app-supplied `provision_membership` hook) now runs
for `group`/`broadcast` too, not only `managed`/`object_thread` — both at creation and on every
authorized re-open (`trigger="reconcile"`), so a group/broadcast conversation's membership stays live
instead of frozen at its creation-time snapshot. The realtime `conversation_upsert` frame still
publishes only for a genuinely new conversation, not on every re-open. A migration widens the existing
partial unique constraint (defensive data-preservation step included; no live consumer had used this
code path before this fix, confirmed via `create_conversation`'s single call site). Design doc updated
to document idempotent join-on-reuse and to confirm `MessagingScope.Kind.OBJECT` already generically
supports a per-entity object scope with no dcm change needed — an app's own group-like entity gets a
stable scope via `get_or_create(kind=OBJECT, content_type=..., object_id=...)`, same mechanism any
object scope already uses.

## [2.38.0] — 2026-07-31

### Added

#### MSG-2d — readable thread reply state, managed-conversation identity

Messages now carry viewer-independent `reply_count`/`last_reply_at` (a soft-deleted reply still counts, matching its tombstone rendering), so a freshly-mounted client can tell a root has replies without expanding every thread. The requesting user's own `thread_last_read_at` is added REST-only, on top of the viewer-independent projection, never on a realtime frame. `serialize_conversation` now exposes `external_key`, letting a client distinguish managed/broadcast conversations that otherwise share the same `kind`. Reply counts are annotated/aggregated and thread receipts bulk-fetched per page — no N+1 across a message list.

### Fixed

#### MSG-2e — declared messaging runtime dependencies

Declared `Pillow>=10`, required by the attachment image decode, EXIF-strip, safe re-encode, and thumbnail pipeline, and `cryptography>=42`, which backs messaging encryption at rest. Both were already imported by the messaging package; `cryptography` had previously been supplied only transitively through `django-allauth`.

## [2.37.0] — 2026-07-31

### Added

#### MSG-2c — poll read contract, conversation preview, realtime frame completion

Polls now expose a viewer-independent encrypted core projection in messages and mutation responses, with REST-only `voted_option_ids`; conversations include a bounded decrypted last-message preview. Added commit-safe messaging realtime fan-out for conversation, reaction, poll, receipt, archive, and membership updates while keeping frame payloads viewer-independent.

## [2.36.1] — 2026-07-31

### Fixed

**MSG-2b — scoped first-contact direct messages**

- A scoped direct conversation can now be opened with a target who has no existing `ConversationParticipant` row in the resolved app. Tenant selection remains entirely server-side (`scope.app`, or the single active app for an omitted scope), while the registered `MessagingPolicy.can_open_direct()` hook remains the sole authorization decision for who may be addressed inside that tenant and still runs before any conversation or participant row is created. Self-DMs remain rejected by the core service.

## [2.36.0] — 2026-07-31

### Added

**MSG-2 — shared messaging platform (`django_core_micha.messaging`)**

- New additive-only subpackage implementing the design in `docs/design/messaging-platform.md` (MSG-1): a consumer-agnostic, multi-tenant messaging domain generalized from jg-ferien's local implementation. No app-specific code lives in dcm — every app-specific decision (who may DM whom, who may post, recipient resolution, moderation rights, managed-membership provisioning) goes through an app-registered `MessagingPolicy` protocol.
- **Domain model:** `MessagingApp` (tenant registry), `MessagingScope` (container/object/global anchor), `Conversation` (direct/group/broadcast/managed/object_thread), `ConversationParticipant`, `Message`, `MessageReaction`, `MessageAttachment`, `MessageThreadReceipt`, `Poll`/`PollOption`/`PollVote`, `MessagingAuditEvent` — the four v1-extension seams (object-thread kind, delivered watermark, retention fields, participant channel/archive state) are schema now, per the design.
- **Encryption at rest, fail-closed by construction:** `MESSAGING_KEYRINGS[app_key]` provisions a distinct ordered Fernet ring per app (`MultiFernet`, rotation-capable); registration validates every ring (non-empty, valid keys, no key shared across apps) and refuses to serve an unregistered or invalidly-configured app rather than falling back to plaintext. Message text, poll content, and attachment blobs/thumbnails/filenames are all encrypted under the owning app's ring; the field deliberately does not auto-decrypt on ORM read — decryption only happens after an authenticated policy check, never on hydration.
- **Tenant resolution is server-side, never client-supplied:** a request never carries an `app_key`. Given a scope, the tenant is `scope.app`; given no scope (a global DM), the tenant resolves to the single active `MessagingApp` registration, failing closed (400/409) if zero or more than one exist — the schema stays genuinely multi-tenant while the ambiguous case is a server misconfiguration, not a client-supplied trust input.
- **REST + realtime contract:** the full `/api/messaging/` surface (conversations, direct/group/broadcast/managed/object-thread creation, messages, replies/threads, reactions, polls, read/delivered watermarks, archive/preferences, scope config, unread counts, attachment upload/download) with opaque signed cursor pagination and `Idempotency-Key`/`client_request_id`-based retry safety; realtime frames on the existing Layer-1 `messaging` WebSocket envelope (`push_to_users`) — no new WebSocket consumer, no client-to-server WS path anywhere, matching the design's explicit non-goal.
- **Privacy invariants enforced at the service layer, not just documented:** DMs never expose per-recipient read-receipt detail, even to a caller holding `read_receipt_detail`; the audited break-glass content-read path (`MessagingAuditEvent`, denial included) is unconditionally denied on DIRECT conversations regardless of moderation rights, since that capability governs read-status visibility, not content decryption. Soft-deleted messages clear `body`/`title`/`link_target` (not just `deleted_at`), and the `message_deleted` realtime frame carries only `message_id`/`deleted_at`/`deleted_by` — never content.
- **Attachments:** magic-byte allowlist (PDF, OOXML, ODF, PNG/JPEG/GIF/WebP) via `filetype`-based content-aware detection — including the OOXML/ODF-vs-bare-ZIP distinction, which `filetype` handles by inspecting internal archive members, not just the outer ZIP signature; images are decoded, EXIF-stripped, safely re-encoded, and thumbnailed before encryption; a `MessagingScanHook` seam is defined (no scanner installed in v1); downloads are always authenticated, viewer-gated, `nosniff`, and download-only (no inline rendering, no generic `/media/` exposure).
- **`notify()` gains a new keyword-only `expires_at=` parameter** (the underlying `Notification.expires_at` field and `prune_notifications` janitor query already existed; this exposes the capability through the public API for the first time — closes the NOTIF-21 gap). Messaging's own notification type (`{app_key}.messaging.new_message`, per-app opt-in registration) uses a 30-day TTL and jg's proven recipe verbatim: `email`+`push` only, no `chip`, `feed_visible=False`, sender/muted excluded via live recipient resolution, delivery failure isolated from the durable message (an exception in the notify path is caught and logged, never rolls back the already-committed message). Sensitive preview content (title/body excerpt) travels only through `transient=`, never persisted into `Notification.content`.
- Fully additive: no existing table altered, no existing `notify()` call site's behavior changed (new parameter defaults to `None`, byte-identical existing behavior).

## [2.35.0] — 2026-07-30

### Added

**NOTIF-19 — transient dispatch params + feed-visibility policy**

- `notify()` accepts a new keyword-only `transient=` mapping. Its values reach the email and push dispatchers for rendering but are **never** written to `Notification.content` and never enter the `dedup_key`. This makes "deliver it, but do not persist it" expressible for the first time: a consuming app whose source field is encrypted at rest (jg-ferien's `Message.body`/`title` are Fernet `EncryptedTextField`s with an audited privileged-read path) can now render an excerpt into an email or push without leaving a cleartext copy in an unencrypted table.
- The values travel through the `ctx` parameter that `Dispatcher.deliver` has always declared but that `dispatch()` never populated — no dispatcher signature changed; the seam was already there.
- **`ChipDispatcher` and `PopupDispatcher` deliberately ignore `ctx`.** They broadcast `notification.content` over the WebSocket, so keeping them clean is the point of the feature; a transient value must never reach the wire. Covered by a negative test.
- `_render_content` merges `transient` over `content["params"]` (transient wins on a key collision) into a new dict, leaving the persisted content untouched.
- `_render_content` now logs a warning when a `.format()` call fails and it falls back to the raw source key. The fallback behaviour itself is unchanged — it previously happened silently, which with transient params would ship a literal `{excerpt}` as an email body with no signal.
- `NotificationType` gains `feed_visible: bool = True`, plus an `iter_feed_hidden_type_keys()` accessor. `CanonicalInboxView` and `CanonicalUnreadCountView` now additionally exclude registered types that set it `False`, alongside the existing `category="todo"` exclusion. This lets a type be delivery-only (email/push) without appearing in the canonical bell feed — previously omitting the `chip` channel suppressed only the realtime push, while the REST feed still listed every recipient row.
- **Exclusion is by explicit registration, never by absence.** A `Notification` whose `notification_type` is no longer registered stays visible, so historical rows are unaffected.
- Fully backward compatible: both features default to today's behaviour. A caller passing neither argument produces a byte-identical persisted row, the same dispatch calls (`ctx=None`), and identical feed contents. Verified additionally that `exclude(field__in=<empty set>)` emits no `WHERE` clause at all, so the 13 consumer repos that register no hidden type see no query change.

## [2.34.0] — 2026-07-30

### Added

**NOTIF-12 — popup channel delivery**

- `PopupDispatcher.deliver` now actually delivers instead of logging a "pending" stub: it sends the same `push_to_users(notification_envelope({...}))` shape as `ChipDispatcher`, with a new `"channel": "popup"` field inside the payload so a client can tell the two apart (the envelope itself stays the NOTIF-13 domain-level `"notification"` discriminator — no second envelope value was introduced).
- `ChipDispatcher` gains the same `"channel": "chip"` field for symmetry. Both dispatchers also now include `"recipient_id"` (the `NotificationRecipient` pk) — `feed/mark/` resolves ids against that model, not `Notification`, and only the recipient pk lets a WS-pushed notification be marked seen/dismissed correctly before the next REST feed refresh.
- Backward compatible: a payload with no `channel` field (an app pinned to an older dcm) keeps behaving exactly as before (feed entry + unread increment) on the ucm side.
- **The popup channel ships with zero producers.** No notification type in dcm, jg-ferien, cockpit, hram, spesix, or survey_app declares `eligible_channels: ["popup"]`, so `resolve_channels()` will not route anything to it until an app opts a type in — this release only wires the dispatcher and is inactive until then. Proven end-to-end by a test-local notification type only (`test_dispatch.py`).
- `prefs.py`, `router.py`, and `resolve_channels` are unchanged — popup was already a valid channel there.

## [2.33.0] — 2026-07-30

### Added

**NOTIF-13 — realtime envelope discriminator**

- Every WS payload `push_to_users` sends for this app now additionally carries `"envelope": "notification"`, via a new `delivery.notification_envelope()` authoring helper — additive only, all existing fields (`type`, `notification_id`, `status`, `content`, etc.) are unchanged.
- Lets a consumer's Layer-1 realtime primitive (ucm 2.13.0's `subscribe(envelope, handler)`) route this domain's messages without misreading a second stream (e.g. messaging) as a notification. A payload with no `envelope` field (older dcm) is still treated as a notification by that primitive's default, so this is fully backward-compatible in both directions.
- `push_to_users`'s own signature is unchanged; only the two existing producers (`views.py`'s status-change broadcast, `dispatch.py`'s `ChipDispatcher`) now wrap their payload through the helper before sending.

## [2.32.0] — 2026-07-29

### Added

**`TodoOverride.created_by` audit field**

- Added `created_by` (nullable FK, `SET_NULL`, `related_name="+"`) to `TodoOverride`, migration `0006_todooverride_created_by` — additive, no default touching existing rows, no unique-constraint change.
- Restores audit attribution (which user set an override) that jg-ferien's NOTIF-10 cutover onto canonical `TodoOverride` would otherwise have silently dropped versus the legacy `EventTaskOverride.created_by` it replaced. Never exposed via any API response.

## [2.31.0] — 2026-07-28

### Added

**Cloudflare Turnstile bot-check for fully-open self-signup (SEC-2)**

- New `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` settings (`env(default="")`) and `auth/turnstile.py` (stdlib `urllib` Cloudflare siteverify call, fail-closed on any missing/invalid token, network error, or hostname mismatch against `ALLOWED_HOSTS`).
- Enforced only inside `register_request` when `TURNSTILE_SECRET_KEY` is set AND the signup mode is `self_signup_open` or `self_signup_email_domain` — every other mode, and every consumer that has not configured the secret, is completely unaffected (pure no-op on this bump).
- `build_public_auth_config()` exposes `turnstile_site_key` only when the secret is configured — this is the single signal the frontend (ucm) uses to decide whether to render the widget at all.
- No new dependency (stdlib only); no `AuthPolicy` schema change.

## [2.30.0] — 2026-07-28

### Changed

**`sync-secrets` now has an explicit local/GitHub destination matrix and retires `.env.local`**

- No flags now writes the full, regenerable local `.env` first when `project.yaml` defines a `local` environment, then syncs every `config.bare_server_targets` GitHub environment. This is a behaviour change for existing automation: a bare invocation in a project with a local environment now also overwrites `.env`.
- `--local` writes only that full composed `.env`; `--github` and its alias `--remote` sync all configured GitHub targets without touching `.env`; `--staging`, `--production`, and legacy `--server --secret-target` retain their single-GitHub-target behaviour.
- Local generation now delegates to `generate-env --env local`, so `.env` includes platform and project composition such as ports, `PROJECT_NAME`, `TRAEFIK_ROUTER_RULE`, `app_env`, and resolved secrets. `.env.local` is no longer created.
- When a project has no `local` environment, bare mode reports a skip and continues its GitHub sync; explicit `--local` remains a fatal configuration error.

## [2.29.0] — 2026-07-27

### Added

**NOTIF-8 / NOTIF-8b — shared derived-todo channel**

- Added the generic derived-todo channel: the engine mechanics (due-expression evaluation, windowing, severity, materialization) lifted domain-free into `notifications/todo/`, a code-first provider registry (`register_todo_provider` / `TodoSeed` / `TodoTypeConfig`), and a `TodoOverride` model (per-scope enable + lead-days).
- Todos are canonical `Notification`s in `mode="derived"`: dismiss/done map onto `NotificationRecipient`, the digest sent-log onto `NotificationDelivery` — no parallel status tables.
- Self-healing on read (NOTIF-8b): the `feed/` surface derives currently-emitted todos live (excludes stored `category="todo"` rows), so a todo whose provider stops emitting disappears immediately; the digest scan additionally reconciles/deletes stale todo overlays.
- Added a generic window-scan/digest management command for derived-todo reminders.
- Dormant until a consumer app registers a todo provider — no behavior change for apps that do not.

## [2.28.0] — 2026-07-27

### Added

**NOTIF-5 — canonical notification read API and status synchronization**

- Added the canonical `feed/`, `feed/unread-count/`, and `feed/mark/` endpoints over `NotificationRecipient`, including flattened message content, per-user status filtering, and self-scoped seen/dismissed/done marking.
- Added the stable `notification.status` WebSocket payload contract so status changes synchronize across notification surfaces.
- Retained the legacy swappable-model `inbox/*` endpoints unchanged during the expand-contract transition.

## [2.27.0] — 2026-07-18

### Added

**NOTIF-1 through NOTIF-4 — canonical notifications platform core**

- Added canonical `Notification`, per-user `NotificationRecipient`, and per-channel `NotificationDelivery` records with deduplication, expiry, and category × channel preferences.
- Added code-first notification type policies, preference-aware routing, and the `notify()` authoring API.
- Formalized chip, email, web-push, todo, and popup dispatchers behind a registry with a bounded synchronous retry hook for dispatchers that report a transient failure; a channel failure never aborts sibling channels. No dispatcher currently shipped reports a transient failure (the existing transports swallow per-recipient errors internally), so retry is forward-looking scaffolding for now, not an active behavior on any channel.
- Added `prune_notifications` for expired and retention-aged canonical notifications (90 days by default), including cascading recipient and delivery cleanup.

### Changed

- Replaced the nullable `NotificationDelivery` uniqueness rule with partial constraints so immediate deliveries (`digest_threshold=None`) are race-safe while distinct digest thresholds coexist.

## [2.26.0] — 2026-07-16

### Fixed

- Bound each `/api/healthz` dependency check with a shared deadline, returning a fast 503 with per-check timeout attribution when a dependency stalls. During a sustained DB outage, wedged non-daemon healthz worker threads accumulate at roughly one per Kuma poll interval and are joined at interpreter exit; the request timeout does not bound them, so graceful shutdown relies on the process manager's SIGKILL grace period as a backstop.
- Added app-wide Redis socket/connect timeouts (2s), a Postgres connection timeout (5s), and Postgres TCP keepalives (30s idle, 10s interval, three probes). The keepalives detect a black-holed Postgres peer in roughly 60 seconds, bounding how long a single wedged healthz worker persists; a hard Redis outage now makes cached-db session cache writes fail fast while reads degrade to the database.
- Retried GitHub secret pushes with exponential backoff and now exit non-zero after reporting failed key names.

## [2.25.0] — 2026-07-15

### Added

**`pwa_install` universal onboarding step (registration only — UI lands in
`ui-core-micha`)**

Registers `pwa_install` in `UNIVERSAL_STEP_KEYS` alongside `cookie_consent`,
`complete_name`, and `browser_push`, so it is created with `enabled=True` by
default and independently toggleable per event via the existing
`OnboardingStepConfigView` admin PATCH endpoint — full parity with the other
universal steps. This is a backend registration change only; the actual step
UI (install prompt, `beforeinstallprompt` capture) is implemented in
`ui-core-micha`, gated there behind an explicit per-app opt-in prop so it
does not start appearing in apps that haven't verified their PWA manifest/
icons are actually installable.

## [2.24.0] — 2026-07-13

### Added

**`sync-secrets`: configurable bare-mode server targets**

Bare `sync-secrets` (no arguments) previously always iterated the hardcoded pair
`(staging, production)`. That fits app repos with one server per environment, but
not infra repos like `webapp-management` that manage several named servers — there
the phantom `production` target failed to resolve. `secrets.yaml` may now set
`config.bare_server_targets` to a non-empty list of server target names; bare mode
iterates those in order. Absent the key, behaviour is unchanged (`staging` then
`production`), so app repos are unaffected. Malformed values abort before any sync.

### Fixed

**Missing hard dependencies caused every published-package install (and the
publish CI's own test gate) to crash on import**

`settings_base.py` does a top-level `from corsheaders.defaults import
default_headers` and registers `corsheaders`, `rest_framework`, `channels`,
`allauth` (+ `.mfa`/`.account`/`.socialaccount`/providers), `whitenoise`
(`MIDDLEWARE` + `STORAGES`), and — transitively, via allauth's Google social
provider — `PyJWT` — none of which were declared in `pyproject.toml`'s
`dependencies`. Every consuming app happened to already install these itself,
masking the gap, until the publish workflow's own test gate (added in the
previous commit) installed `django-core-micha` in isolation and failed with
`ModuleNotFoundError: No module named 'allauth'`.

The first fix pass only added `django-environ`, `django-cors-headers`,
`djangorestframework`, `channels`, `channels_redis`, and `django-allauth[mfa]`.
Independent review correctly flagged that the existing test suite never
actually imports `settings_base.py` (`tests/settings.py` hand-rolls a minimal
settings module instead — see `test_channel_layer.py`), so passing tests were
not evidence that `corsheaders`/`whitenoise` were fixed. Added
`tests/test_settings_base_dependencies.py`, a regression test that imports
`settings_base` for real in an isolated subprocess and explicitly resolves
every `MIDDLEWARE`/`STORAGES` backend string (since `django.setup()` alone
does not import those lazily-referenced paths). Running it against the
reviewed fix caught `whitenoise` immediately, and after adding that, a second
gap: `ModuleNotFoundError: No module named 'jwt'` (needed by
`allauth.socialaccount.providers.google`, registered in `CORE_APPS`
unconditionally).

A second independent review pass then found a third, still-latent gap:
`requests` (also required by the Google OAuth2 provider) was only present by
accident, via `django-anymail`'s own unrelated transitive dependency on it —
the exact same masking pattern this whole fix exists to eliminate. Rather than
hand-declaring `requests`/`PyJWT` separately, switched to
`django-allauth[mfa,socialaccount]` — allauth's own `socialaccount` extra
already declares `oauthlib`, `requests>=2.0.0`, and `pyjwt[crypto]<3,>=2.0`
together, which is the exact transitive set `settings_base.py` needs and is
self-documenting instead of hand-rediscovered.

Verified three times over, each via a real disprove-then-reprove cycle in a
freshly created virtualenv (uninstall the package, confirm
`test_settings_base_dependencies.py` fails with the exact expected
`ModuleNotFoundError`, reinstall, confirm the full suite passes again) — for
`whitenoise`, `jwt`, and `requests` in turn. Final verification: a completely
fresh virtualenv, installed purely via `pip install -e ".[test]"` against the
final `pyproject.toml`, full suite green (295 passed). A full AST-based scan
of every top-level third-party import across all of `src/django_core_micha`
(not just `settings_base.py`) found nothing else missing; the one remaining
unscanned import (`asgiref`, used by `notifications/delivery.py`) is safe —
it's an unconditional hard dependency of Django itself, already covered by
`Django>=6.0.5`.

## [2.22.0] — 2026-06-15

### Added

**`/api/healthz`: three new readiness checks — migrations, config, version**

The health endpoint now covers the deploy-error classes that `db` + `cache`
cannot see:

| Check | What it catches | 503 on failure |
|---|---|---|
| `migrations` | Unapplied migrations — schema stale after deploy | yes |
| `config` | Missing critical config keys (e.g. `RESEND_API_KEY` when `EMAIL_PROVIDER=resend`) | yes |
| `version` | `APP_GIT_SHA` env var — stale-image detection for CI | no (info only) |

Implementation notes:

- **migrations**: uses `MigrationExecutor(connection).migration_plan(leaf_nodes)` —
  read-only, no `call_command('migrate')`, one DB query.
- **config**: checks key *presence* only; values are never serialised.
  Which keys are required is derived from live settings (`EMAIL_PROVIDER`,
  `AUTH_METHODS`), so apps without Resend or social login never produce a
  false-503.  Response shape: `{"ok": bool, "missing": ["KEY_NAME", …]}`.
- **version**: `{"version": "<sha>" | null}` at the top level — Kuma/CI can
  assert "running == pushed" without the endpoint knowing the expected SHA.

Response is backward-compatible: `status`, `checks.db`, and `checks.cache`
shapes are unchanged; `migrations` and `config` are new entries in `checks`;
`version` is a new top-level field.

A brief `degraded` window during the migrate-then-up deploy cycle is expected
and acceptable.

## [2.20.0] — 2026-06-11

### Changed

**`sync-secrets` bare invocation syncs both environments**

`sync-secrets` called with no arguments now runs a full GitHub-secrets sync for
`staging` and then `production` in sequence (identical code paths to the explicit
`--server --secret-target <target>` invocations). If the staging pass fails, the
production pass is skipped and the CLI exits non-zero.

Two new shorthand flags are also available:

| Flag | Equivalent to |
|---|---|
| `--staging` | `--server --secret-target staging` |
| `--production` | `--server --secret-target production` |

Explicit invocations (`--server --secret-target <target>`, `--local`) are
unchanged. Note: bare `sync-secrets` now mutates both GitHub environments on
every call — use the explicit flags when you only want to touch one.

## [2.19.0] — 2026-06-10

### Added

**Redis-backed sessions (`cached_db` on Redis db 2)**

`settings_base` now configures `CACHES["default"]` using Django's built-in
`RedisCache` backend, pointing at `redis://<REDIS_HOST>:6379/2` (db 2 — db 0
is used by the Channels pubsub layer and Celery broker in consumer apps; db 1
is commonly used by Celery result backends). `SESSION_ENGINE` is set to
`django.contrib.sessions.backends.cached_db` so session reads are served from
Redis while writes still hit Postgres. A Redis flush therefore costs only cache
misses, never logouts. No new infrastructure is required: all consumer apps
already declare a hard dependency on Redis via the Channels layer.

### Migration

- **No code change required** in consumer apps — the settings are applied
  automatically via `settings_base`.
- The Redis instance of each consumer app will start serving session reads on
  **db 2**. Apps that pin `maxmemory` on their Redis instance should account for
  the additional session key space (typically small, proportional to active
  concurrent sessions).
- The test settings override `CACHES` to `LocMemCache` so the CI test suite
  remains green without a Redis instance.

## [2.18.2] — 2026-06-10

### Fixed

**Query explosion in `BaseUserSerializer` for admin users**

`_admin_policy_satisfied` re-fetched the auth policy from the DB on every call —
~10x per `/api/users/current/` serialization (8 ui_permissions helpers +
`can_manage_support_agents` + `security_state`). Now memoized per request
(`request._dcm_admin_policy_cache`, invalidated by `set_security_level`).
`get_user_security_state` now reads all authenticator types in one query
instead of three `.exists()` calls. Permission semantics unchanged.

## [2.18.0] — 2026-06-09

### Added

**Pluggable Mail-Transport — `EMAIL_PROVIDER`**

`settings_base` now selects the email backend via the `EMAIL_PROVIDER` env var:

| `EMAIL_PROVIDER` | Backend | Required env vars |
|---|---|---|
| *(empty)* | `console` (IS_LOCAL/DEBUG) · `smtp` (otherwise) | same as before |
| `console` | Django console backend | — |
| `smtp` | Django SMTP backend | `EMAIL_HOST`, `EMAIL_PASSWORD` |
| `resend` | anymail Resend backend | `RESEND_API_KEY` |
| `postmark` | anymail Postmark backend | `POSTMARK_SERVER_TOKEN` |

**No crash on missing credentials.** When `EMAIL_PROVIDER` is set but the required credential is absent, a `warnings.warn` + `logger.warning` is emitted and the backend falls back to console — the app always boots.

**`EMAIL_PORT` / `EMAIL_USE_TLS`** now carry explicit defaults (`587` / `True`) at the call site; API-transport providers no longer crash when these vars are absent.

**`DEFAULT_FROM_EMAIL`** is now configurable via `env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)`.

### Migration

- No change required for existing apps (no `EMAIL_PROVIDER` set → identical behavior).
- API transports (Resend/Postmark): set `DEFAULT_FROM_EMAIL` explicitly to your verified sender address (e.g. `DEFAULT_FROM_EMAIL=noreply@km-h.ch`) — `EMAIL_HOST_USER` will be empty for these providers and would produce an invalid From address.
- `django-anymail>=10` is now a required dependency; it is included automatically when bumping `django-core-micha`.

## [2.17.4] — 2026-06-02

### Fixed

**Auditlog — `AuditEvent.objects.create()` failure breaks outer DB transaction**

When `metadata` contained a non-JSON-serializable value (e.g. a raw `UUID` from a `context_resolver` that forgot `str()`), the `TypeError` from psycopg2's JSON adapter propagated through Django's `mark_for_rollback_on_error` context manager inside `_save_table`, setting `connection.needs_rollback = True`. The `except Exception` block in `_create_audit_event` then caught the error and logged it, but the outer transaction was already poisoned — every subsequent query in the same test (or request) failed with `TransactionManagementError`.

Fix: wrap `AuditEvent.objects.create()` in `transaction.atomic()`. Inside an existing transaction this creates a savepoint; if the create fails, only the savepoint is rolled back and the outer transaction remains intact.

### Migration required

- App bump `django-core-micha` → `==2.17.4` in `backend/requirements.txt`. No data migration.
- App `context_resolver` lambdas that return FK IDs should wrap with `str(...) if ... is not None else None` to avoid silent audit-write failures.

## [2.17.3] — 2026-05-31

### Fixed

**WebSocket channel layer — periodic `redis.exceptions.TimeoutError` crashing consumers**

`CHANNEL_LAYERS` used `channels_redis.core.RedisChannelLayer`, whose BRPOP-based receive loop raises `redis.exceptions.TimeoutError` ("Timeout reading from redis:6379") on idle WS connections with current redis-py / Python 3.14. Consumers crashed in a ~5s `WSDISCONNECT` loop, flooding logs and breaking live updates.

Switched to `channels_redis.pubsub.RedisPubSubChannelLayer`, which uses a persistent SUBSCRIBE instead of polling. All consumers across apps use only group semantics (`group_add`/`group_discard`/`group_send`), which the pub/sub layer fully supports — no consumer changes required.

### Migration required

- App bump `django-core-micha` → `==2.17.3` in `backend/requirements.txt`, then redeploy. No data migration.

Behavioural notes for WS-using apps (none of the current apps are affected — all use group-only consumers via standard ASGI dispatch):

- **Fire-and-forget:** no per-channel message capacity/backpressure. Apps relying on `channel_layer.receive()` on individual channels would need review.
- **No `group_expiry` TTL:** group membership is in-process and cleaned up on `disconnect()` (`group_discard`). A hard process crash leaves stale membership until restart — the old layer's 24h TTL had no equivalent here. Immaterial for short-lived connections.
- **Strip legacy `CONFIG` keys** from any app-level `CHANNEL_LAYERS` override before bumping: `RedisPubSubChannelLayer` rejects `expiry` / `group_expiry` / `capacity` / `channel_capacity` with a `TypeError` at consumer startup.
- **`group_add` requires standard ASGI dispatch:** the pub/sub layer registers the channel via `new_channel()` during dispatch; tests that poke the channel layer directly (outside `WebsocketCommunicator`) must call `new_channel()` before `group_add()`.

## [2.17.2] — 2026-05-31

### Fixed

**S212 follow-up — `ACCOUNT_RATE_LIMITS` 500 on every login (`ratelimit configured per user but used anonymously`)**

The S212 rate-limit config used the `/user` rate key for actions that allauth evaluates in an anonymous context. allauth consumes the `login_failed` limit inside `pre_authenticate` (before any user is known); a `/user` component there raises `ImproperlyConfigured`, surfacing as **HTTP 500 on every login attempt** in non-local environments. `password_reset` and `confirm_email` were affected the same way (both reachable while logged out).

Changed the anonymous-context limits to key on `/ip` and `/key` (the submitted identifier) instead of `/user`:

- `login_failed`: `5/5m/ip,10/h/user` → `5/5m/ip,10/h/key`
- `confirm_email`: `3/h/user` → `3/h/key`

Also fixed a latent typo: the reset-password limit was keyed `password_reset`, which is **not** an allauth action name — allauth merges this dict over its defaults and silently ignores unknown keys, so the entry had never taken effect (allauth's own `20/m/ip,5/m/key` applied instead). Renamed to the canonical `reset_password` and made it anonymous-safe: `5/h/ip,3/h/key`.

`reauthenticate` and `manage_email` keep `/user` (only reachable when authenticated). The dict is now exposed as `ACCOUNT_RATE_LIMITS_DEFAULTS`, and the regression test guards both the anonymous-context invariant and that every action name matches an allauth canonical key.

### Migration required

- App bump `django-core-micha` → `==2.17.2` in `backend/requirements.txt`, then redeploy. No data migration.

## [2.16.0] — 2026-05-28

### Added

**S211 / S212 / S213 — Audit-Log-Erweiterung: AuthN-Events, Brute-Force-Mitigation, DRF-AuthZ-Logging**

#### S211 — AuthN-Events persistent loggen

New signal receivers in `django_core_micha.auth.signals` create `AuditEvent` entries for every authentication lifecycle event. All events store a k-anonymised IP (`/24` for IPv4, `/48` for IPv6), a coarse UA family, and a session-key digest (sha256[:16]) — no full IP, no full UA string, no session secret.

New `event_type` strings (searchable in `AuditEvent.event_type`):

- `users.user.logged_in` — successful login
- `users.user.logged_out` — explicit logout
- `users.user.login_failed` — failed login attempt; metadata contains `credential_hash` (sha256[:8] of lowercased input), never plaintext
- `users.user.password_changed` — password updated while logged in
- `users.user.password_set` — password set for the first time (social-only → local)
- `users.user.password_reset` — password reset via email link
- `users.user.email.confirmed` — email address confirmed; metadata contains `email_domain`
- `users.user.email.added` — additional email address added; metadata contains `email_domain`
- `users.user.email.removed` — email address removed; metadata contains `email_domain`
- `users.user.mfa.authenticator_added` — MFA method enrolled; metadata contains `authenticator_type` (e.g. `totp`, `webauthn`, `recovery_codes`) — no secret/seed
- `users.user.mfa.authenticator_removed` — MFA method removed
- `users.user.mfa.authenticator_reset` — MFA method reset (e.g. recovery codes regenerated)
- `users.user.social.added` — social account linked; metadata contains `provider` + `uid`
- `users.user.social.removed` — social account unlinked
- `users.user.social.updated` — social token refreshed

MFA signal connections are deferred to `AppConfig.ready()` so the signals module is importable even when `allauth.mfa` is not in `INSTALLED_APPS`.

New helpers in `django_core_micha.auth._audit_helpers`: `_client_ip`, `_ua_family`, `_session_key_digest`, `_credential_hash`.

#### S212 — Failed-Login-Tracking / Brute-Force-Mitigation

Added `ACCOUNT_RATE_LIMITS` to `settings_base.py` using allauth's built-in Redis-backed rate limiter. Disabled in `IS_LOCAL` environments to avoid dev/test friction.

Configured limits:

| Key | Limit |
|---|---|
| `login_failed` | `5/5m/ip, 10/h/user` |
| `login` | `30/m/ip` |
| `signup` | `10/h/ip` |
| `password_reset` | `5/h/ip, 3/h/user` |
| `reauthenticate` | `10/m/user` |
| `confirm_email` | `3/h/user` |
| `manage_email` | `10/h/user` |

#### S213 — DRF AuthZ-Denial-Logging

Extended the existing `custom_exception_handler` in `django_core_micha.auth.exception_handler` to persist `AuditEvent` entries for access-control failures:

- `drf.not_authenticated` (HTTP 401)
- `drf.permission_denied` (HTTP 403)
- `drf.throttled` (HTTP 429) — metadata includes `retry_after` in seconds

All three include `view` (class name), `action` (ViewSet action or `None`), `method`, `path`. Actor is set to the authenticated user where available, `None` for anonymous. Audit write failures are logged but never abort the response.

### Migration required

None — all changes are signal receivers and settings. No new models.

## [2.15.1] — 2026-05-28

### Fixed

**S198 / auditlog — `models.E034` index-name-too-long blocked all consumer apps from migrating after dcm 2.15.0 bump**

The `AuditEvent` index `auditlog_event_type_created_idx` (31 characters) exceeded Django's default 30-character limit for index names. Django's system-check failed with `models.E034` before migrations could run, breaking deploys in every app that bumped to dcm 2.15.0. Renamed to `auditlog_evtype_created_idx` (27 chars); since 2.15.0 had not yet successfully deployed anywhere in production (the index never landed in any database), the initial migration is edited in place rather than chaining a rename-migration.

### Migration required

- App bump `django-core-micha==2.15.0` → `django-core-micha==2.15.1` in `backend/requirements.txt`.
- No data migration; the renamed index lands cleanly on first migrate.

## [2.15.0] — 2026-05-28

### Added

**S198 — Platform AuditLog (`django_core_micha.auditlog`)**

New app providing a reusable business-audit-event pattern for all platform apps.

- `AuditEvent` model with actor FK, `event_type`, `metadata` JSON (model, object_id, action, changes, before, after, request_id), `created_at`
- `register(model, redact_fields, context_resolver)` API — apps declare tracked models in `<top_app>/audit_config.py`, loaded automatically via `AuditlogConfig.ready()`
- `AuditlogActorMiddleware` — sets actor + request_id ContextVars per request (X-Request-ID header, falls back to generated UUID)
- Field-diff via pre/post-save signals; raw state captured, PII redaction applied before persistence (so PII-only changes are still recorded as events)
- `prune_audit_events` management command — `--days` override, `--dry-run`; default from `AUDITLOG_RETENTION_DAYS` setting (default 730 days)
- `AUDITLOG_RETENTION_DAYS` added to `settings_base.py` (env-tuneable per app)
- `AuditlogActorMiddleware` wired into `settings_base.py` MIDDLEWARE after `AuthenticationMiddleware`

## [2.14.0] — 2026-05-22

### Added

**S112 — WebSocket Permission Framework (`django_core_micha.auth.ws_permissions`)**

- `BaseSecureConsumer` — base Django Channels consumer with built-in permission checks
- `IsAuthenticated`, `IsObjectOwner`, `AllowAnonymous` permission classes
- `WSPermissionInventory` — startup check that all consumers declare permissions
- `generate-env` script: `ENV_TYPE` now defaults to `production` (fail-safe)
