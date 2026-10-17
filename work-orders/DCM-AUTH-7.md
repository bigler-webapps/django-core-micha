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

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and the places that change together. One dispatch; complete, no stubs. This is an authentication change in shared core: when in doubt, refuse the admin.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md`, `tests/settings.py` (version, changelog and register are the Orchestrator's). Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/auth/admin_mfa_middleware.py`, `AdminMfaRequiredMiddleware`: `__call__` returns 403 when `_needs_mfa_check(request)` and not `_has_mfa(request.user)`. `_needs_mfa_check` is False when `ADMIN_MFA_REQUIRED` is False (explicit opt-out, default True), when the path does not start with `/admin/`, when `settings.IS_LOCAL` is True (local dev bypass), when the user is anonymous, or when the user is not a superuser. `_has_mfa(user)` checks only that a non-recovery `allauth.mfa.models.Authenticator` row EXISTS for the user (lazy import). The module docstring documents the bootstrap flow and the staging note. This is the S267 gap: an enrolled authenticator is enough even if the session was authenticated by password only, staff users who are not superusers are not checked at all, and Django's own admin login (`admin.site.login`, `AuthenticationForm`) calls `authenticate()` directly so allauth's MFA stage and its login rate limit never run.
- allauth (installed 65.19.7, read the source rather than trusting this paragraph): `allauth.account.authentication.get_authentication_records(request)` returns the session's list of authentication records (`request.session["account_authentication_methods"]`), each a dict like `{"method": "password", "at": ts, ...}` or `{"method": "mfa", "at": ts, "id": 1, "type": "totp"}` (types `totp`, `webauthn`, `recovery_codes`); `record_authentication` is called from `allauth/account/internal/flows/login.py`, `allauth/mfa/base/internal/flows.py` (the MFA step), reauthentication and social login flows. `allauth.account.decorators.secure_admin_login(function=None)` wraps a view: an authenticated user who is not `is_staff`/`is_active` gets `PermissionDenied`, an anonymous one is redirected to `settings.LOGIN_URL` with `next`, an authenticated staff user passes through. Confirm the record shape and the MFA record's `type` values in `allauth/mfa/base/internal/flows.py` before relying on them.
- `src/django_core_micha/auth/apps.py`, `CoreAuthConfig.ready()` (label `django_core_micha_auth`): imports signals, connects MFA signals when `allauth.mfa` is installed, connects the auth-policy cache receivers. This is where "without per-app code" wiring belongs. `auth/security.py` has dcm's own session security level (`get_security_level(request)` / `set_security_level`); do NOT use it for this check, use allauth's authentication records.
- Tests: `tests/test_admin_mfa_middleware.py` (existing unit tests of the middleware with a fake `allauth.mfa.models` module injected into `sys.modules`: opt-out flag, non-admin URL, local env, anonymous, non-superuser, superuser without MFA -> 403, with TOTP/WebAuthn -> pass, recovery codes only -> 403). The test settings do NOT install `django.contrib.admin`; build the admin wiring so it can be tested on a standalone `django.contrib.admin.sites.AdminSite()` instance.

### Fixed design

1. **Session MFA helper** in `admin_mfa_middleware.py` (module level, used by the middleware and importable): `session_used_mfa(request) -> bool` — true only if `get_authentication_records(request)` contains at least one record with `method == "mfa"` whose `type` is not `"recovery_codes"` (recovery codes are a fallback, consistent with the existing exclusion; a record without a `type` counts as not sufficient). Lazy import of allauth inside the function. Never raises (a malformed session value is "no MFA"). Records are read from the session only: a password-only session, a social-login-only session and a reauthentication record are all "no MFA".
2. **Middleware rule.** For a request under `/admin/` by an authenticated user, the existing opt-out, path, local-bypass and anonymous skips stay. The user class check changes from "superuser only" to "superuser OR staff" (`is_staff`). For those users the request passes only if BOTH hold: `_has_mfa(user)` (an enrolled non-recovery authenticator, unchanged) AND `session_used_mfa(request)`. Otherwise 403 with a message that tells the person to log in again through the normal login with their second factor (the response never states which condition failed beyond "this session was not authenticated with MFA" vs "no MFA factor enrolled": two short texts are fine). Non-staff non-superuser users keep being left to Django's own gate. Non-admin URLs and anonymous requests are untouched.
3. **Admin login through allauth, no per-app code.** A function `secure_admin_site(site)` (new module `auth/admin_login.py` or next to the middleware) replaces `site.login` by `allauth.account.decorators.secure_admin_login(site.login)` on the given `AdminSite` instance, idempotently (calling it twice must not wrap twice; mark the wrapper). `CoreAuthConfig.ready()` calls it for the default `django.contrib.admin.site` ONLY when `django.contrib.admin` is installed and `allauth.account` is installed (guard both with `apps.is_installed`; no import of `django.contrib.admin` when it is not installed). The result: an unauthenticated visit to the admin login URL is redirected to `settings.LOGIN_URL` (the app's allauth/SPA login, with MFA and allauth's login rate limit) with `next` preserved, and Django's password form is never shown or processed; apps with a custom `AdminSite` can call `secure_admin_site(their_site)` (documented in the function docstring, not automated). Do NOT change `LOGIN_URL`, URLs or the SPA login.
4. **Unchanged:** `ADMIN_MFA_REQUIRED` opt-out, `IS_LOCAL` bypass, path prefix `/admin/`, recovery-code exclusion, bootstrap behaviour of apps that do not install the admin, every non-admin URL. Update the module docstring to describe the new rule and the bootstrap flow (log in through the normal login with MFA first). No new setting, no model or migration change.

### Tests (new file `tests/test_admin_session_mfa.py`; update `tests/test_admin_mfa_middleware.py` where its premise changes)

Existing middleware tests must be adapted, not deleted: a superuser with an enrolled authenticator now also needs an MFA record in the session; keep every other existing assertion (opt-out, non-admin URL, local bypass, anonymous, recovery-codes-only still refused). New tests: `session_used_mfa` unit cases (no records, password only, social only, `mfa` totp, `mfa` webauthn, `mfa` recovery_codes only, malformed session value, record without type); middleware: superuser enrolled + password-only session -> 403, superuser enrolled + MFA record -> pass, staff non-superuser enrolled + password-only -> 403, staff non-superuser enrolled + MFA record -> pass, staff not enrolled but MFA record in the session -> 403, non-staff non-superuser -> left alone, anonymous -> left alone, `IS_LOCAL` -> bypass, `ADMIN_MFA_REQUIRED=False` -> bypass, non-admin URL with a password-only superuser -> unaffected; admin login wiring on a standalone `AdminSite()`: after `secure_admin_site(site)` an unauthenticated GET and POST (with a username/password body) to `site.login` are redirected to `LOGIN_URL` with the `next` parameter, the Django `AuthenticationForm` is never validated and `authenticate()` is never called (patch `django.contrib.auth.authenticate` to fail the test if called), an authenticated active staff user passes through, an authenticated non-staff user gets `PermissionDenied`, calling `secure_admin_site` twice wraps once; the rate limit is allauth's on its own login view and is not re-tested here (state that in a comment: the point is that the admin no longer has its own, unthrottled password check). Build requests with `RequestFactory` and a session dict or `SessionMiddleware`; use `override_settings(LOGIN_URL=...)`.

### Invariants

The admin is reachable only with a session that used a real MFA factor (non-recovery) AND an enrolled authenticator; staff are included; Django's bare password check no longer serves the admin; everything outside `/admin/` behaves as before; no per-app code is needed; failing safe (any doubt = refuse).

### Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S267; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
