# APP_FINDINGS.md — django-core-micha

Generated 2026-05-24 from a deep-security audit (sec_reviewer agent pass).
Cross-reference: `webapp-management/SECURITY_FINDINGS.md` for the central tracking.

This is the platform auth-library — bugs here are amplified across all 7 consumer apps.

Already addressed:
- S7, S11, S13, S16, S17, S18, S22, S26, S30, S40, S41, S42, S44, S50, S51, S52/S53, S54, S64, S65, S67, S70 Phase F, S71

---

## P2 — Major

### S163 — DCM-NEW-recoveryrequest-crud-fallthrough — `RecoveryRequestViewSet` accepts CRUD from any auth user
**Severity:** P2
**File:** `src/django_core_micha/auth/views.py:637-666` (`RecoveryRequestViewSet.get_permissions`)
**Confidence:** high
**Issue:** `get_permissions()` has no case for `create`, `update`, `partial_update`, `destroy`. These actions fall through to `super().get_permissions()` → platform default `IsAuthenticated`. Any authenticated user can:
- POST `/api/support/recovery-requests/` → fabricate a RecoveryRequest for any user (serializer's `user` FK is writable on create)
- PATCH a pending request → mutate `message` before support reviews
- DELETE any recovery request
The doc note "S111 closed by platform-default bump to 2.12.0" was **wrong** — moving the default to `IsAuthenticated` does not gate these write actions. (`read_only_fields` blocks `status`/`user` on update, but `message` remains writable.)
**Repro:** Auth as any user, `POST /api/support/recovery-requests/` with `{"user": <victim_id>, "message": "spoof"}` → row created.
**Fix:** Either:
1. Override `create/update/partial_update/destroy` to raise `MethodNotAllowed` (recovery requests should be created by the dedicated `create_from_mfa` action only), or
2. Switch from `ModelViewSet` to `ReadOnlyModelViewSet` + explicit `@action` declarations for the lifecycle endpoints.

### S164 — DCM-NEW-recovery-token-in-fragment — Plaintext token in redirect fragment + API response
**Severity:** P2
**File:** `src/django_core_micha/auth/views.py:78` (`recovery_complete_view`), `src/django_core_micha/auth/services.py:37` + `views.py:690` (`approve` action)
**Confidence:** high
**Issue:** `recovery_complete_view` places `rr.token` in URL fragment (`#recovery=<token>`). Fragment is retained in browser history, accessible to any JS on the login origin (analytics, error reporters). Additionally, `approve_recovery_request` builds a URL containing the plaintext token, returned in the API response at line 690 as `recovery_link` — leaks through HTTP response logs, API-gateway access logs, proxy response caches. S50 mitigated token-in-URL-path for the final `recovery_login` POST, but the intermediate redirect step and support-facing API response remain uncovered.
**Fix:** Redirect: store token in a short-lived server-side session key; redirect to `/login#recovery=ok` without the token. FE re-fetches the token via a session endpoint. API: omit `recovery_link` from the JSON; support agent doesn't need the raw URL — only the email recipient does.

### S165 — DCM-NEW-recovery-ttl-from-creation — Recovery expiry starts at created_at, not approved_at
**Severity:** P2
**File:** `src/django_core_micha/auth/recovery.py:98` (`RecoveryRequest.expires_at`)
**Confidence:** high
**Issue:** `expires_at = created_at + TTL_MINUTES`. A request pending in PENDING state for 29 of 30 minutes before approval expires within 1 minute of the user receiving the approval email. Practically unusable token with no user feedback.
**Fix:** Add `approved_at` field; set it in `mark_resolved(APPROVED)`; redefine `is_active()` to use `approved_at + TTL` when approved, `created_at + TTL` otherwise.

### S166 — DCM-NEW-healthz-error-leak — DB error text returned in healthz response
**Severity:** P2
**File:** `src/django_core_micha/health/views.py:47` (`healthz_view`)
**Confidence:** high
**Issue:** Returns `"error": str(exc)[:200]` in JSON response. PostgreSQL exceptions can include `FATAL: password authentication failed for user "X"`, connection-string fragments, schema/host names. The endpoint has no auth gate (intentionally public for Uptime Kuma).
**Fix:** Replace `str(exc)[:_ERROR_TRUNCATE]` with generic string (e.g. `"database check failed"`); log the full exception server-side at ERROR level.

### S167 — DCM-NEW-account-email-verification-optional — `ACCOUNT_EMAIL_VERIFICATION = "optional"` platform default
**Severity:** P2
**File:** `src/django_core_micha/settings/settings_base.py:323`
**Confidence:** medium
**Issue:** Allauth's headless login endpoint (`/api/auth/browser/v1/auth/login`) does not gate on `email_verification` for password logins. `"optional"` means an admin-invited user (`EmailAddress.verified=False`) can authenticate via allauth headless without consuming the invite link / completing email-ownership proof. The custom `register_confirm` flow sets `verified=True`, but admin-invite path in `InviteActionsMixin.invite` creates `EmailAddress` with `verified=False`. The `InvitationOnlySocialAdapter` comment references this gap defensively.
**Fix:** Set `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` platform-wide; ensure invite-flow sets `verified=True` only after the invite link is consumed (already done in `PasswordResetConfirmView.post`).

---

## P3 — Tracking

### S168 — DCM-NEW-recovery-active-deadbranch
**Severity:** P3
**File:** `src/django_core_micha/auth/services.py:78` (`perform_recovery_login`)
**Confidence:** medium
**Issue:** Queryset already filters `status=APPROVED`. The subsequent expiry branch attempts to mark PENDING-or-APPROVED as EXPIRED, but only APPROVED can arrive here — PENDING branch is dead. Misleading intent could mask a future regression if the filter is widened.
**Fix:** Simplify to `rr.mark_resolved(RecoveryRequest.Status.EXPIRED)` without inner status check.

### S169 — DCM-NEW-throttle-sha1
**Severity:** P3
**File:** `src/django_core_micha/auth/throttles.py:42`
**Confidence:** medium
**Issue:** Uses `hashlib.sha1` for throttle cache key derivation. Not security-sensitive (cache bucket key), but inconsistent with all other hashing in the codebase (HMAC-SHA-256). Future reviewer might conflate.
**Fix:** Replace with `hashlib.sha256`.

---

## ui-core-micha findings (in this repo's audit pass)

### S170 — UICORE-NEW-passwordinvite-open-redirect
**Severity:** P2
**File:** `src/pages/PasswordInvitePage.jsx:74` (in `ui-core-micha`)
**Confidence:** high
**Issue:** `nextPath` read from `location.search` is composed into `/login?next=${encodeURIComponent(nextPath)}` without validating that it starts with `/`. An attacker crafts `/invite/<uid>/<token>?next=https://evil.example/`; after password-set the user is redirected to `/login?next=https%3A%2F%2Fevil.example%2F`. The `LoginPage` does gate `requestedNext.startsWith('/')`, but defense-in-depth requires origin-sanitization at composition, not only at consumption.
**Fix:** Before encoding: `if (!nextPath || !nextPath.startsWith('/') || nextPath.startsWith('//')) { nextPath = '/'; }`.

---

## Residual risks / lower-confidence

- `RecoveryRequestViewSet` CRUD fallthrough is the highest-priority server-side gap.
- Plaintext token in fragment + API response is exploitable wherever the support-facing API response is logged or where login page hosts third-party JS.
- `ACCOUNT_EMAIL_VERIFICATION = "optional"` default creates a gap for admin-invite flow specifically.
- The auditing agent reported `tests/` absent from src/ — note: tests/ lives at repo root (`tests/test_*.py`), 104 tests pass. Agent missed this because of relative-path-search heuristic. Not a finding.
