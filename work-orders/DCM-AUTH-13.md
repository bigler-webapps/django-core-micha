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

## Context package

Work from this package; do not explore broadly from scratch; open only the named files to verify. If you must dig deeper, delegate to a read-only Explore sub-agent.

**Established cause (verified against the allauth wheels, not release notes).** `session_used_mfa` (`src/django_core_micha/auth/admin_mfa_middleware.py:32`) calls `allauth.account.authentication.get_authentication_records(request)`. In allauth 65.15.0 and 65.20.0 that function is `request.session.get(AUTHENTICATION_METHODS_SESSION_KEY, [])`. In allauth 65.21.1 (defined in `allauth/account/internal/flows/login.py`, re-exported from `allauth.account.authentication`) the signature is `get_authentication_records(request, user=None)` and it returns `[]` when `user` is None or has no pk, and also when `user_id_to_str(user)` differs from the user id stored in the session under a second private key, `account_authentication_methods_user`. So on 65.21 the current call can never see a record: every real MFA login would be refused. That is the production defect; the failing test only exposes it, because it seeds the private key `account_authentication_methods` by hand and calls the real function. The writer, `allauth.account.internal.flows.login.record_authentication(request, user, method, **extra_data)`, exists on every version, is what `allauth.mfa` calls for a completed MFA step (`record_authentication(request, authenticator.user, "mfa", **extra_data)` with `type` and `id`), and on 65.21 refuses a user that is None, not authenticated or without a pk, and stores the user binding. dcm declares `django-allauth` with no lower bound, so the check has to work on both reader signatures (the old `(request)` and the new `(request, user=None)`).

**Named files to change**

- `src/django_core_micha/auth/admin_mfa_middleware.py`, function `session_used_mfa` only. The reader must be called with the request's user on versions that accept one, and without it on versions that do not (decide by the reader's signature, not by catching a `TypeError` that could hide other errors). Everything else in `session_used_mfa` stays: the list/tuple check, the allowlist `("totp", "webauthn")` on `type` with `method == "mfa"`, and the fail-closed `except`. An anonymous or missing user is "no MFA". Do not touch `AdminMfaRequiredMiddleware`, `_has_mfa`, `_needs_mfa_check` or `_is_admin_request`.
- `tests/test_admin_session_mfa.py`: `_install_authentication_records` (line 24, replaces `allauth.account.authentication` in `sys.modules` with a one-argument lambda) and every place that seeds `request.session = {"account_authentication_methods": ...}` by hand (lines 50, 57, 95, 258, 267, 334). `test_session_used_mfa_record_cases` (line 34) is the parametrized record table.
- `tests/test_admin_mfa_middleware.py`: the fake `allauth.account.authentication` installed at lines 101-107 and `_record_mfa` (line 116) seed the same private key by hand.

**Tests to write and how.** Create records through the real `record_authentication(request, user, "mfa", type="totp", id=1)` (import from `allauth.account.internal.flows.login`), with a real user model instance (`get_user_model()(pk=..., username=...)`, unsaved is fine; it has `is_authenticated` True and a pk) and a request whose `session` is a plain dict. Stop replacing `allauth.account.authentication` in `sys.modules` for the tests that decide the record outcome: they must run against the real allauth reader. Passwords and recovery codes are recorded the same way (`method="password"`, `method="mfa"` with `type="recovery_codes"`). The "other user" case: record for user A, then call with `request.user` = user B. The "aborted login" case: record, then `allauth.account.internal.flows.login.clear_authentication_records(request)`, then the check fails. Keep the existing cases that cover malformed data and unknown or missing types fail-closed; those may keep seeding the raw key where the point is the malformed shape, but they must still run through the real reader on the installed allauth (on 65.21 a hand-seeded list without the user-binding key is correctly ignored, so seed it through `record_authentication` plus a direct mutation of the stored list, or assert only the False outcome knowingly). The enrolled-factor mock of `allauth.mfa.models` (`_install_authenticators`) is not part of this order.

**Invariants and pitfalls**

- Acceptance stays an allowlist; do not widen it to make a test green.
- Do not count a record that allauth's own reader would not attribute to `request.user`.
- The tests must pass on allauth 65.15.0 (installed here) and 65.21.1 (the Orchestrator runs that one). Do not use an allauth API that only one of them has beyond `record_authentication`, `clear_authentication_records` (65.21; absent on 65.15, so for the aborted-login case on 65.15 drop the session keys directly) and `get_authentication_records`; where a call differs by version, branch on the reader signature as in the source change.
- `pyproject.toml`: do not add an allauth lower or upper bound.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha`. The tree also carries foreign uncommitted edits in `src/django_core_micha/scripts/sync_secrets.py` and `tests/test_sync_secrets.py`: leave them alone.

## Required test run (yours, once)

`python -m pytest tests/test_admin_session_mfa.py tests/test_admin_mfa_middleware.py -q`

## Preamble

The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI beyond what the spec says; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` or `CHANGELOG.md` or `pyproject.toml` — the register row, the release notes, the version and the review verdicts are the orchestrator's alone.** **Your tools are for editing source and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests the `Required tests` section calls for AND RUN the tests you just wrote to confirm they execute and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the authoritative set and does the independent review after you finish — those are the gate; your own run does not count as the gate.

Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final `RESULT: DONE|BLOCKED <reason>`.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line. Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no reviewers, no verification run, no register edit, no commit.** You ARE the implementation instance described below; do NOT launch or dispatch another implementer.

## Execution directive

Configured `implementation` entry from `.claude/models.local.json`: codex / gpt-6-luna, CLI route (`codex exec`, explicit `-m gpt-6-luna`, banner read back). `.claude/codex-status.md` has no line for 2026-11-03, so Codex is used. Launch evidence: recorded in the register Notiz after the run.

## Review routing

Tier 3 in shared-core auth: lenses `envelope`, `regression`, `duplication`, `tests` plus `sec_review`, all concurrent, codex/gpt-6-luna read-only, one fix round and a closing pass on `envelope` and `tests`; the Orchestrator reads the diff against Part A while they run.

## Verification

Orchestrator runs `tests/test_admin_session_mfa.py`, `tests/test_admin_mfa_middleware.py` and the auth security tests (`test_client_ip.py`, the permission/ws inventory tests) on allauth 65.15.0 (installed) and on allauth 65.21.1 (a separate `--target` install on `PYTHONPATH`, never into the user site), then the full suite once as the publish gate's equivalent, justified as a shared-core dependency-ripple exception.

## Register + commit

- The publish after landing is the normal push-triggered `publish.yml` run; a manual dispatch still needs the operator's confirmation naming the workflow.
- 3.20.0 was never published, so no further version bump: the existing `## [3.20.0]` entry in `CHANGELOG.md` gains a Fixed note for this order. Record in the register which allauth versions the gate tests ran on, and the review line.
