# DCM-AUTH-10 — Admin MFA gate becomes opt-in per app (adjusts DCM-AUTH-7; S267, S19)

# A. Envelope

**Goal.** Make the admin MFA requirement a decision each app takes for itself. Expected outcome: dcm ships
`ADMIN_MFA_REQUIRED = False` as the default. An app that sets it to `True` gets the complete 3.11.0 gate
(enrolled TOTP or WebAuthn, an MFA-authenticated session, superusers and staff). An app that sets nothing
has no admin MFA gate. The allauth-routed admin login from DCM-AUTH-7 (`auth/admin_login.py`: login rate
limit, no Django password form) stays on for every app, independent of the setting.

**Background.** Since S19 the middleware `auth/admin_mfa_middleware.py` enforced an enrolled factor for
superusers with `ADMIN_MFA_REQUIRED = True` as the default in `settings/settings_base.py` (opt-out).
DCM-AUTH-7 (3.11.0) added staff users and the session factor on the same default. The operator does not
want the requirement imposed estate-wide by the next pin bump: many apps have admins without an enrolled
factor. Only fitness-monitor opted out explicitly.

**Scope (operator decision 2026-10-17).**
1. **Opt-in default.** `ADMIN_MFA_REQUIRED = False` in `settings/settings_base.py`, and the middleware's own
   fallback for a missing setting becomes False as well, so an app without the setting is not gated in any
   environment. The settings comment and the middleware docstring describe the opt-in.
2. **With `ADMIN_MFA_REQUIRED = True`** the 3.11.0 behaviour is unchanged: staff included, session factor
   required, recovery codes excluded, `IS_LOCAL` bypass.
3. **Admin login** keeps running through allauth regardless of the setting.
4. **Release.** Minor version bump with a CHANGELOG entry under Security that states the consumer effect:
   an app that wants admin MFA sets `ADMIN_MFA_REQUIRED = True`; without it neither the DCM-AUTH-7 session
   requirement nor the S19 enrolment requirement applies.

**Non-goals / do not touch.** Per-app settings (each app decides in its own order); the auth-policy model
(`required_auth_factor_count` is not read by this gate); the SPA login and MFA enrolment; the MFA gate on
admin API actions (`_admin_policy_satisfied`).

**Risks.** Every app that has not set the flag loses the S19 enrolment check it has had since dcm 2.x once
it bumps; the operator accepts this as the per-app decision, and the register notes S19 and S267 as
per-app from then on. fitness-monitor's explicit `False` stays valid.

**Required tests (write as part of the implementation; `tests/test_admin_mfa_middleware.py` and
`tests/test_admin_session_mfa.py`).**
- Settings without the flag, non-local: a superuser without any authenticator and a staff user without any
  authenticator pass the middleware on `/admin/`.
- The setting absent from `settings` entirely (middleware fallback): passes.
- `ADMIN_MFA_REQUIRED = True`: the existing cases are unchanged (enrolment and session factor required,
  staff included, recovery codes excluded, `IS_LOCAL` bypass).
- The default admin site is still wired through allauth with the flag False.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (change inside shared core, authentication).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the exact places. One dispatch; complete, no stubs. This is an authentication default change in shared core: with the flag True the gate must be byte-for-byte what it is now.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's), `tests/settings.py`, and the files `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` (another session's uncommitted work: leave them exactly as they are). Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/settings/settings_base.py` (about lines 126-136): the middleware list ends with `AdminMfaRequiredMiddleware` (comment block "S19: Enforce MFA enrollment for Django admin in non-local envs. Opt-out via `ADMIN_MFA_REQUIRED = False`"), followed by `ADMIN_MFA_REQUIRED = True` with the comment "S19: Set to False to disable admin-MFA enforcement (not recommended in prod)". Keep the middleware in the list unchanged (it stays a no-op while the flag is False).
- `src/django_core_micha/auth/admin_mfa_middleware.py`: module docstring (describes the gate: skipped when `ADMIN_MFA_REQUIRED=False`, non-admin paths, `IS_LOCAL`, anonymous; operators log in with a second factor first), `AdminMfaRequiredMiddleware._needs_mfa_check` begins with `if not getattr(settings, "ADMIN_MFA_REQUIRED", True): return False` (the fallback `True` is what makes an app without the setting gated), then `IS_LOCAL`, the user checks, then `_is_admin_request`. `session_used_mfa`, `_has_mfa` and `_is_admin_request` stay as they are.
- `src/django_core_micha/auth/admin_login.py` and `auth/apps.py` (`CoreAuthConfig.ready()` calls `secure_default_admin_site()`): the allauth-routed admin login. It is independent of the flag and must stay so.
- Every place that reads the flag: the two files above only (`grep -rn ADMIN_MFA_REQUIRED src` confirms; docs and the CHANGELOG mention it, which are the Orchestrator's). Existing tests: `tests/test_admin_mfa_middleware.py` and `tests/test_admin_session_mfa.py` set `ADMIN_MFA_REQUIRED` explicitly in almost every test (`override_settings`); the ones that rely on the old default (a request with no explicit setting that expects a gated outcome) must be given an explicit `ADMIN_MFA_REQUIRED=True` rather than the assertions weakened.

### Fixed design

1. `settings_base.py`: `ADMIN_MFA_REQUIRED = False`; rewrite the two comments to describe the opt-in ("set to True to require an enrolled TOTP/WebAuthn factor and an MFA-authenticated session for superusers and staff in the Django admin; default False: each app decides; the allauth-routed admin login is always on"). The middleware stays in the list.
2. `admin_mfa_middleware.py`: the fallback for a missing setting becomes False: `getattr(settings, "ADMIN_MFA_REQUIRED", False)`. Update the module docstring: the gate is opt-in (`ADMIN_MFA_REQUIRED = True`), what it requires, the bootstrap/operator note, and that the allauth admin login (`auth/admin_login.py`) is on regardless of the flag. Nothing else in the middleware changes.
3. With the flag True nothing changes (enrolled non-recovery factor, MFA-authenticated session, superusers and staff, `IS_LOCAL` bypass, path and admin-namespace detection, fail-closed behaviour). `admin_login.py` and `apps.py` are not touched.

### Tests (extend `tests/test_admin_mfa_middleware.py` and `tests/test_admin_session_mfa.py`; existing assertions stay)

Settings without the flag set at all in the test (use `settings` fixture `del settings.ADMIN_MFA_REQUIRED` or an override that removes it so the middleware's fallback is exercised; ALSO one test that imports the shipped default from `django_core_micha.settings.settings_base` and asserts it is `False`): non-local, a superuser without any authenticator and a staff user without any authenticator both pass the middleware on `/admin/` and on a prefixed admin (`ROOT_URLCONF="tests.urls_backend_admin"`, `/backend/admin/`), and a superuser with a password-only session passes too; the setting present as `False` passes (existing); the setting `True` keeps every existing refusal (enrolment, session factor, staff, recovery codes excluded, unknown MFA types, script prefix, prefixed admin) and the `IS_LOCAL` bypass (re-assert a representative subset with an explicit `True`); `secure_default_admin_site()` still wires the default admin site with the flag False and with it absent (the wiring test already patches `apps.is_installed`; add the flag cases); an anonymous or non-staff request is untouched in both modes.

### Invariants

Without the setting there is no admin MFA gate in any environment; with `True` the gate is exactly the current one; the allauth admin login is wired regardless of the flag; no other behaviour changes.

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

Source: operator decision in the requirements round of session `local_95ff6b21-4aba-46a9-8285-0f1560f0a734`
on 2026-10-17 (MFA stays a per-app decision; opt-in per setting). Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. Independent of DCM-AUTH-8/9; may ship in the same minor
release. On landing, update the S267 and S19 status lines in the register (per-app opt-in).
