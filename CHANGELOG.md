# Changelog

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
