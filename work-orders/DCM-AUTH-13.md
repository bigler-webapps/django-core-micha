# DCM-AUTH-13 — Admin MFA session check under allauth 65.20+

# A. Envelope

## Goal and expected outcome

The release of 3.20.0 (DCM-SEC-4, `ee4c166`, closes the `CF-Connecting-IP` rate-limit finding) did not publish: its pytest gate failed on `tests/test_admin_session_mfa.py::test_staff_with_an_mfa_session_but_no_enrolled_factor_is_refused`. CI resolved `django-allauth` 65.21.1; the test passes on 65.15.0. It seeds the raw session key `account_authentication_methods` with `{"method": "mfa", "type": "totp"}` and expects `session_used_mfa(request) is True`; under 65.21.1 the result is `False`.

allauth changed how authentication records are kept in 65.20.0 and 65.21.0. Both are security releases: aborted login flows discard their records, and records no longer carry over between users. The cause is not yet established: either only the test fixture no longer matches how allauth stores and reads records, or `session_used_mfa` no longer recognises a real MFA login. The second case would refuse every staff/superuser on apps with `ADMIN_MFA_REQUIRED=True` once they build with allauth 65.20+. That fails closed (lockout, not bypass), but it is still a defect.

Outcome:

1. The cause is established against allauth's own record-writing and record-reading API on the resolved 65.21.x, not inferred from release notes.
2. `session_used_mfa` returns True for a session in which a real TOTP or WebAuthn MFA step was completed for the current user, and False otherwise. Unchanged rules: recovery codes do not count, unknown or missing types fail closed, malformed data and exceptions fail closed.
3. The tests create MFA records through allauth's API instead of writing allauth's private session key by hand, so they track allauth's storage instead of freezing one version's layout.
4. The next published django-core-micha release carries both DCM-SEC-4 and this fix.

## Scope

- `src/django_core_micha/auth/admin_mfa_middleware.py` (`session_used_mfa`), only as far as the established cause requires.
- `tests/test_admin_session_mfa.py` and any other test that seeds allauth authentication records by hand.
- The version and changelog entry needed to publish.

## Non-goals / do not touch

- No upper bound or downgrade pin on `django-allauth` below 65.21. 65.20 and 65.21 carry security fixes that consumers must receive.
- No change to the gate's policy: who is gated, the opt-in `ADMIN_MFA_REQUIRED`, `IS_LOCAL`, admin path detection, and the enrolled-factor check stay as they are.
- No change to DCM-SEC-4's client-IP code.
- No app pin bumps; those are separate orders per app.
- The local environment mismatch in `test_settings_base_is_importable_with_only_declared_dependencies` (Django 6.0.5 without `DEPRECATED_EMAIL_SETTINGS`) is not part of this order.

## Risks

- **Silently broadening acceptance.** A fix that makes the test green by accepting more record shapes than allauth writes for a completed MFA step would weaken the gate. Acceptance stays an allowlist.
- **Records of another user.** 65.21 binds records to the user who authenticated. The check must not count a record that allauth itself would not attribute to `request.user`.
- **Version spread across consumers.** Apps resolve different allauth versions. The check must be correct on the version range dcm declares, at minimum the lowest supported version and 65.21.x.

## Required tests to write (scoped)

1. A session with a completed TOTP MFA step, recorded through allauth's API for the current user, passes `session_used_mfa`; a WebAuthn record also passes.
2. A session with only a recovery-code record, with a password-only record, or with no records fails.
3. A record belonging to another user, or one allauth would discard after an aborted login, fails.
4. The existing gate tests (staff without an enrolled factor refused, superuser/staff with both passes) run on records written through allauth's API.
5. Run the module's tests on both the lowest declared allauth version and 65.21.x.

## Tier

Tier 3 — inside shared-core (`django-core-micha`), auth.

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

_Not yet filled. The Orchestrator fills the context package, target working directory, progress contract and preamble here before any dispatch. Dispatch is blocked while this placeholder remains._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

- The publish after landing is the normal push-triggered `publish.yml` run; a manual dispatch still needs the operator's confirmation naming the workflow.
- Record in the register which allauth versions the gate tests ran on.
