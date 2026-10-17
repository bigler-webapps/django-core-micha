# DCM-AUTH-7 — Admin login through allauth, MFA required in the session (closes S267)

# A. Envelope

**Goal.** Close S267 from the 2026-10-17 estate audit (`webapp-management/SECURITY_FINDINGS.md`). Expected
outcome: the Django admin is reachable only with a session that was authenticated with MFA, and the admin
login runs through allauth (MFA step, login rate limit) instead of Django's bare password check.

**Background (from the finding).** `auth/admin_mfa_middleware.py` `_has_mfa()` checks that the user has an
MFA authenticator enrolled, not that the current session used it. Django's admin login form calls
`authenticate()` directly, so neither allauth's MFA stage nor its `login_failed` rate limit (in
`DefaultAccountAdapter.authenticate()`) applies. Staff users who are not superusers are not checked at all;
allauth's `secure_admin_login` is used nowhere in the estate. A leaked or guessed superuser password opens
`/admin/` without a second factor and without throttling, and that session also authenticates the SPA API.

**Scope.**
1. dcm routes the admin login through allauth (`secure_admin_login` or equivalent) for every app that mounts
   the admin, without per-app code.
2. The admin MFA middleware admits superusers and staff users to the admin only when the current session was
   authenticated with MFA; an enrolled authenticator alone no longer suffices.
3. Minor release with a CHANGELOG entry (the admin needs an MFA login; staff included).

**Non-goals / do not touch.** The SPA login flow; whether apps mount the admin; app pin bumps (separate);
S250/S251.

**Risks.** Locking the operator out of the admin: sessions without an MFA login are refused and must log in
again through allauth with MFA; apps with a custom admin site; tests that enter the admin with `force_login`.

**Required tests (write as part of the implementation).**
- Admin login with the correct password alone → no admin session without the MFA step.
- A session authenticated by password only, MFA enrolled → admin refused; a session authenticated with MFA →
  admin allowed; a staff user who is not a superuser → same rules.
- Repeated failed admin logins → allauth's rate limit applies.
- Non-admin URLs are unaffected.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (shared core, authentication).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

_To be filled by the Orchestrator (context package, preamble, progress contract) before dispatch._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S267; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
