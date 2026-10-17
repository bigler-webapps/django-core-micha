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

_Filled by the Orchestrator on `git pull`: context package, target working directory, progress contract
and the preamble block. Not dispatchable while this placeholder stands._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: S249 (`webapp-management/SECURITY_FINDINGS.md`), requirements round in session
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734` on 2026-10-17. Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. Release order: this order first, then DCM-AUTH-9 and
DCM-AUTH-10; the three may ship in one minor release. On landing, update the S249 status line in the
register.
