# Changelog

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
