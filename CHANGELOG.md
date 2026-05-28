# Changelog

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
