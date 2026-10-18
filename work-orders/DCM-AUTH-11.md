# DCM-AUTH-11 — Generic user create only for users admins; dead strong-security helpers removed (closes S246, S251)

# A. Envelope

**Goal.** Close S246 and the remainder of S251 from `webapp-management/SECURITY_FINDINGS.md`. Expected outcome:
`POST /api/users/` on `BaseUserViewSet` creates an account only for superusers and users admins, unless a consumer
changes that through one documented hook; a create without username and e-mail answers 400 and writes nothing;
`RequireStrongSecurity` and `require_security_level` no longer exist.

**Background.**
- S246: `BaseUserViewSet` (`auth/views.py`) sets `permission_classes = [IsAuthenticated]` and defines neither
  `get_permissions()` nor `create()`. The non-admin scoping in `get_queryset()` covers update and destroy (with the
  DCM-AUTH-2/5/9 rules), not create. Any authenticated user creates a user row (empty username and e-mail, active,
  unusable password); the second such request ends in an unhandled 500 on the unique username.
- Consumers that use the route today: jg-ferien's CSV user import (its UI is shown to superusers only), innoservice
  (its own `perform_create` requires the `USERS_MANAGE` capability), bigler-consult (its own `get_permissions`
  returns `BackofficeOwner` for `create`).
- S251, corrected 2026-10-18: `AuthPolicy.admin_required_auth_factor_count` is effective, through the admin auth
  policy gate (`_admin_policy_satisfied` in `auth/permissions.py`). What remains of the finding:
  `RequireStrongSecurity` (`auth/permissions.py`) and `require_security_level` (`auth/security.py`) do nothing
  unless `SECURITY_ENFORCE_STRONG_AUTH` is set, and have no call site in dcm or in any of the 19 consuming apps
  (measured 2026-10-18 on each app's `develop`). They read like a protection that is not there.

**Scope (operator decisions 2026-10-18).**
1. **Create permission.** The generic create is allowed for superusers and for users admins
   (`can_view_users_admin(user, request=request)`, request-aware, so the admin auth policy applies). Every other
   authenticated user gets 403 and no row is written. The permission is decided before payload validation.
2. **Consumer hook.** One documented, overridable method on `BaseUserViewSet` decides whether the requester may
   create; its default is rule 1. A consumer may widen or narrow it. Consumer guards that exist today
   (innoservice's `perform_create`, bigler-consult's `get_permissions`) keep working unchanged.
3. **Payload validation.** A permitted create without username and without e-mail answers 400 with a field error
   and writes nothing, instead of the 500.
4. **Removal.** `RequireStrongSecurity` and `require_security_level` are removed. The setting
   `SECURITY_ENFORCE_STRONG_AUTH` stays (it still decides the default factor count in `auth/policy.py`).
5. **Unchanged.** The invite flow and `invite_link`; update and destroy rules (DCM-AUTH-2/5/9); role assignment
   through `RolePolicy`; queryset scoping; signup, reset and recovery actions; security levels, the admin auth
   policy and MFA.
6. **Release.** Minor version bump with CHANGELOG entries under Security (S246: who may use the generic create;
   400 instead of 500) and under Removed (the two helpers; no known consumer uses them).

**Non-goals / do not touch.** App pin bumps (hram's inventory test, S247, is part of HRAM-DEP-8); `create` on other
viewsets; the invite action's own permission; any change to who counts as a users admin.

**Risks.** A consumer whose create actors are not users admins in dcm's sense loses the route unless it overrides
the hook (innoservice: holders of `USERS_MANAGE` below `INVITE_MIN_ROLE_LEVEL = 2` would be refused); each app's
bump checks this. A consumer that imports one of the removed helpers fails at import; none is known in the estate.

**Required tests (write as part of the implementation).**
- Authenticated user without admin rights: `POST /api/users/` with a valid payload → 403, user count unchanged;
  with an empty payload → 403, not 400 (permission before validation).
- Users admin (a level-2 invite admin) and superuser: create with an e-mail → 201; create with an empty payload →
  400 with a field error, user count unchanged.
- Full auth admin with `admin_required_auth_factor_count = 2` and a session that is not strong → 403.
- Hook: a viewset subclass that widens the hook lets a non-admin create; one that narrows it refuses a users admin.
- Importing `RequireStrongSecurity` or `require_security_level` from their former modules fails.
- Existing tests stay green: `tests/test_user_rank_rule.py`, `tests/test_self_edit_delete_restriction.py`,
  `tests/test_auth_policy_threshold.py`, `tests/test_auth_policy_cache.py`.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (change inside shared core, authorization).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

_Filled by the Orchestrator on `git pull`: context package, target working directory, progress contract
and the preamble block. Not dispatchable while this placeholder stands._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: S246 and S251 (`webapp-management/SECURITY_FINDINGS.md`), requirements round in session
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734` on 2026-10-18. Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. On landing, update the S246 and S251 status lines in the register.
HRAM-DEP-8 carries S247 and targets at least the release that contains this order.
