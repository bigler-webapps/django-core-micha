# DCM-AUTH-8 — Auth-policy write threshold without a role hierarchy (closes S249)

# A. Envelope

**Goal.** Close S249 from `webapp-management/SECURITY_FINDINGS.md` (P1, measured in Gustav 2026-08-25).
Expected outcome: in an app without a multi-level `ROLE_DEFINITIONS`, no user gains full auth-admin rights
by role. Only superusers, and roles an app explicitly places at or above `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL`,
read and write the auth policy and pass the gates built on `has_full_auth_admin_rights`.

**Background (from the finding).** `_default_auth_policy_write_level()` in `auth/access.py` returns the
highest level in `get_role_definitions()`. With dcm's default definitions (one role at level 1) that is the
level every user without an explicit role already has, so `has_full_auth_admin_rights` is true for any
authenticated user, and through the OR chain so are `has_invite_admin_rights`, `can_view_users_admin`,
`can_view_invite_admin` and the write side of `BaseUserViewSet.auth_policy`. Gustav, Cinevia and
Photogallery carry the stopgap `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 3` in their settings.

**Scope (operator decision 2026-10-17).**
1. The computed default threshold lies above the level a user without an explicit role has (the level of
   the default role code). When the role definitions offer no level above it (no definitions, a single
   level, or every level equal to the default one), the default threshold is `ROLE_LEVEL_3`.
2. An explicit `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL` keeps precedence over the computed default, in both
   directions, as today.
3. Apps with a real hierarchy (highest level above the default role's level) are unchanged: the highest
   level stays the threshold.
4. Minor release with a CHANGELOG entry under Security that names S249 and the consumer effect: in an app
   without a role hierarchy only superusers hold full auth-admin rights; the three stopgap settings become
   redundant and are removed by those apps' own orders.

**Non-goals / do not touch.** `INVITE_MIN_ROLE_LEVEL` and `INVITE_ADMIN_ROLES` semantics; the MFA gate on
admin actions (`_admin_policy_satisfied`); `RolePolicy`; app settings and app pins (separate orders);
the rank rule (DCM-AUTH-9) and the admin MFA default (DCM-AUTH-10).

**Risks.** An app that relied on the degenerate threshold, where every user may edit the auth policy: none
known, the three apps where it was measured carry the stopgap. Superusers are unaffected (`_has_min_level`
admits them first). An app whose only roles all sit at one level above the default role's level keeps that
level as threshold, which is the hierarchy it declared.

**Required tests (write as part of the implementation; new file next to `tests/test_auth_policy_cache.py`).**
- No `ROLE_DEFINITIONS`: an authenticated user without a role gets `has_full_auth_admin_rights` False,
  `has_invite_admin_rights` False, `can_view_users_admin` and `can_view_invite_admin` False; `GET` and `PUT`
  on `auth_policy` answer 403 and the policy is unchanged; a superuser reads and writes it.
- Single-level definitions (one role, and several roles all at one level) behave the same.
- Two-level definitions (user 1, admin 3): the admin passes, the user does not; unchanged behaviour.
- An explicit `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL` wins: set to 1 without definitions the role-less user
  passes; set to 4 with levels 1 and 3 the level-3 user does not.
- Existing tests stay green: `tests/test_auth_policy_cache.py`, `tests/test_self_edit_delete_restriction.py`,
  `src/django_core_micha/billing/tests/test_operator_endpoints.py`.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (change inside shared core, authorization).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the exact rule and the places to test. One dispatch; complete, no stubs. This is an authorization change in shared core: when in doubt, deny.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's), `tests/settings.py`, and the files `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` (another session's uncommitted work: leave them exactly as they are). Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/auth/access.py`: `_has_min_level(user, setting_name, default_level)` (False for anonymous, True for superusers, otherwise `get_role_level_for_user(user) >= int(getattr(settings, setting_name, default_level))`), `_default_auth_policy_write_level()` (the function to change: it collects the integer `level` of every entry of `get_role_definitions()`, returns `ROLE_LEVEL_3` when there are none, else `max(levels)`), `has_full_auth_admin_rights(user)` (`_has_min_level(user, "AUTH_POLICY_WRITE_MIN_ROLE_LEVEL", _default_auth_policy_write_level())`), `has_invite_admin_rights(user)` (superuser, or full auth admin, or `INVITE_ADMIN_ROLES`, or `INVITE_MIN_ROLE_LEVEL` default level 2), `has_access_code_admin_rights`, `can_manage_support_agents`, `can_assign_support_contact`, `is_subject_to_admin_auth_policy`.
- `src/django_core_micha/auth/roles.py`: `ROLE_LEVEL_0..3` (0 guest, 1 user, 2 manager, 3 owner/org admin), `DEFAULT_ROLE_DEFINITIONS = {"user": {"level": 1}}`, `DEFAULT_ROLE_CODE = "user"`, `get_role_definitions()` (settings `ROLE_DEFINITIONS` or the default), `get_default_role_code()` (settings `DEFAULT_ROLE_CODE` or `"user"`), `get_role_level_for_code(code)` (unknown code -> level 0), `get_role_code_for_user(user)` (profile role or the default role code), `get_role_level_for_user(user)`.
- `src/django_core_micha/auth/permissions.py`: the request-aware wrappers (`has_full_auth_admin_rights(user, request)`, `has_invite_admin_rights`, `can_view_users_admin`, `can_view_invite_admin`, `can_write_auth_policy`, ...) call the base functions above and add the admin auth policy check; `BaseUserViewSet.auth_policy` in `auth/views.py` uses `can_view_auth_policy` / `can_write_auth_policy` (about lines 400-520; read how `GET` and `PUT` answer and which permissions guard them).
- The S249 finding: with the default definitions (one role at level 1) the computed threshold is 1, which every user without an explicit role already has, so every authenticated user is a full auth admin. Three apps carry the stopgap `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 3`.
- Existing tests that must stay green: `tests/test_auth_policy_cache.py`, `tests/test_self_edit_delete_restriction.py` (its fixture sets `ROLE_DEFINITIONS` user=1 / admin=3), `tests/test_user_rank_rule.py` (levels 1/2/3), `src/django_core_micha/billing/tests/test_operator_endpoints.py`, `tests/test_admin_session_mfa.py`.

### Fixed design

1. **The new computed default** in `_default_auth_policy_write_level()`: let `default_level = get_role_level_for_code(get_default_role_code())` (the level a user without an explicit role has; an unknown default code gives 0). Collect the integer levels of the role definitions as today (ignore malformed entries as today). If at least one level is strictly greater than `default_level`, return `max(levels)` (a real hierarchy: unchanged). Otherwise (no definitions, a single level, every level equal to or below the default role's level, or nothing parsable) return `ROLE_LEVEL_3`. Nothing else in the module changes.
2. **Precedence:** an explicit `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL` in settings still wins over the computed default in both directions (it is read in `_has_min_level` through `getattr(settings, ..., default_level)`): do not change that path. The computed default must be evaluated lazily at call time (as today), not at import time.
3. **Effects** (verify, do not special-case): with no definitions or single-level definitions a role-less authenticated user is no longer a full auth admin, hence not an invite admin, not `can_view_users_admin`, not `can_view_invite_admin`, and cannot read or write the auth policy; superusers keep every right; two-level definitions (user 1, admin 3) behave as before.
4. **No other change:** `INVITE_MIN_ROLE_LEVEL`/`INVITE_ADMIN_ROLES` semantics, the admin MFA gate (`_admin_policy_satisfied`), `RolePolicy`, app settings, the rank rule. Add a short comment in `_default_auth_policy_write_level` naming S249.

### Tests (new file `tests/test_auth_policy_threshold.py`, next to `tests/test_auth_policy_cache.py`; existing tests stay green)

Look at `tests/test_auth_policy_cache.py` and `tests/test_self_edit_delete_restriction.py` for the viewset/request helpers and for how `auth_policy` is driven (`APIRequestFactory`, `force_authenticate`, `session`). Use `override_settings`/`settings` for `ROLE_DEFINITIONS`, `DEFAULT_ROLE_CODE`, `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL`, and the roles-patch pattern of `tests/test_user_rank_rule.py` (monkeypatch `django_core_micha.auth.roles.get_role_code_for_user`) when users need different roles. Cases from Part A: (1) no `ROLE_DEFINITIONS` (the default definitions): an authenticated role-less user gets `has_full_auth_admin_rights`, `has_invite_admin_rights`, `can_view_users_admin`, `can_view_invite_admin` all False; `GET` and `PUT` on `auth_policy` answer 403 and the stored policy is unchanged; a superuser reads and writes it; (2) single-level definitions: one role at level 1, and several roles all at level 1 (and all at level 2 with the default role also at level 2: nothing above the default level -> threshold 3): same as (1); (3) two-level definitions (user 1, admin 3, default code user): the admin passes, the user does not; and a three-level definition (1, 2, 4): threshold 4, the level-2 user does not pass, the level-4 user does; (4) an explicit `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 1` without definitions: the role-less user passes (explicit wins downwards); `= 4` with levels 1 and 3: the level-3 user does not (explicit wins upwards); (5) direct unit tests of `_default_auth_policy_write_level()` for: no definitions -> 3, definitions `{}` -> 3, one level -> 3, all equal -> 3, malformed levels only -> 3, 1/3 -> 3, 1/2 -> 2, 1/2/4 -> 4, a default role code that does not exist in the definitions (level 0) with definitions {user:1} -> 1. Prove each refusal leaves the policy row unchanged.

### Invariants

Without a role hierarchy only superusers (and an explicit setting) grant full auth-admin rights; a real hierarchy keeps its highest level as the threshold; an explicit setting always wins; evaluated at call time; nothing else changes.

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

Source: S249 (`webapp-management/SECURITY_FINDINGS.md`), requirements round in session
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734` on 2026-10-17. Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. Release order: this order first, then DCM-AUTH-9 and
DCM-AUTH-10; the three may ship in one minor release. On landing, update the S249 status line in the
register.
