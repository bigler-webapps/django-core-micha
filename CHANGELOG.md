# Changelog

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
