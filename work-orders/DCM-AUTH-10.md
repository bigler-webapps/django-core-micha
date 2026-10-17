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

_Filled by the Orchestrator on `git pull`: context package, target working directory, progress contract
and the preamble block. Not dispatchable while this placeholder stands._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: operator decision in the requirements round of session `local_95ff6b21-4aba-46a9-8285-0f1560f0a734`
on 2026-10-17 (MFA stays a per-app decision; opt-in per setting). Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. Independent of DCM-AUTH-8/9; may ship in the same minor
release. On landing, update the S267 and S19 status lines in the register (per-app opt-in).
