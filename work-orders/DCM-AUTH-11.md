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

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, the order of the checks and the places to test. One dispatch; complete, no stubs. This is an authorization change in shared core: when in doubt, refuse.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's), `tests/settings.py`, and the files `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` (another session's uncommitted work: leave them exactly as they are). Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/auth/views.py`, `class BaseUserViewSet(InviteActionsMixin, viewsets.ModelViewSet)` (about line 189): `permission_classes = [IsAuthenticated]`, `serializer_class = None` (consumers set it), `get_queryset()` (all users for `can_view_users_admin(user, request=self.request)`, else the requester's own row), the guards `_ensure_auth_policy_view`, `_ensure_auth_policy_write`, `_ensure_signup_qr_admin` (each raises `rest_framework.exceptions.PermissionDenied("Permission denied.")`), `update` and `destroy` (DCM-AUTH-2/5/9 rules). There is no `get_permissions()` and no `create()`: `POST` to the list route reaches `ModelViewSet.create`. `PermissionDenied` and the permission helpers (`can_view_users_admin`, `can_view_auth_policy`, ...) are already imported at the top of the file.
- `src/django_core_micha/auth/permissions.py`: `can_view_users_admin(user, request=None)` = `has_invite_admin_rights(user, request=request)`, which is superuser, or full auth admin, or `INVITE_ADMIN_ROLES`, or `INVITE_MIN_ROLE_LEVEL` (default level 2), AND the admin auth policy (`_admin_policy_satisfied`: when the user is subject to the admin auth policy, `is_user_security_sufficient(user, request=request)` must hold, i.e. the session factor level meets the policy's `admin_required_auth_factor_count`). It is request-aware, so the policy applies. This is THE users-admin check. Also in this file: `class RequireStrongSecurity(BasePermission)` (about line 197, with `required_level = "strong"`; allows everything unless `SECURITY_ENFORCE_STRONG_AUTH`) — to be removed.
- `src/django_core_micha/auth/security.py`: `get_security_level`, `set_security_level`, `is_level_sufficient`, `is_user_security_sufficient`, `require_security_level(required)` (a decorator, about line 78, same non-enforcing behaviour) — to be removed. `SECURITY_ENFORCE_STRONG_AUTH` stays: it still decides the default factor count in `auth/policy.py`; do not touch that.
- `grep -rn "RequireStrongSecurity\|require_security_level" src tests` finds only the two definitions (measured). After the removal clean up imports that become unused in those two files, nothing else.
- Consumers (do not edit, they only explain the hook): innoservice overrides `perform_create`, bigler-consult overrides `get_permissions` to return its own permission for `create`; both must keep working: their guards run in DRF's `initial()` (permissions) or `perform_create`, around the new hook.
- Tests to model on: `tests/test_self_edit_delete_restriction.py` and `tests/test_user_rank_rule.py` (viewset subclass with a minimal `ModelSerializer`, `APIRequestFactory`, `force_authenticate`, roles patched through `django_core_micha.auth.roles.get_role_code_for_user`, the autouse settings fixtures with `ROLE_DEFINITIONS`, `INVITE_MIN_ROLE_LEVEL`, `SECURITY_LEVELS`), and `tests/test_auth_policy_threshold.py` / `tests/test_auth_policy_cache.py` for how the auth policy row (`AUTH_POLICY_MODEL = "testapp.AuthPolicyStub"`, field `admin_required_auth_factor_count`) and a session security level are set up (look at `auth/policy.py`, `auth/security.py` `get_security_level` for the session key).

### Fixed design

1. **Hook:** a new overridable method on `BaseUserViewSet`: `def can_create_user(self, request) -> bool:` documented in its docstring as the consumer hook for the generic create. Default: `return can_view_users_admin(request.user, request=request)` (superusers pass inside that function; anonymous users are False; the admin auth policy applies). A consumer may override it to widen or narrow.
2. **Create:** override `create(self, request, *args, **kwargs)` on `BaseUserViewSet`: (a) `if not self.can_create_user(request): raise PermissionDenied("Permission denied.")` — FIRST, before any payload access, so a non-admin sends a valid or an empty payload and gets the same 403 and no row is written; (b) then validate the payload: the stripped `username` and the stripped `email` of `request.data` are both empty or absent -> `raise rest_framework.exceptions.ValidationError({"username": ["Provide a username or an e-mail address."], "email": ["Provide a username or an e-mail address."]})` (400, nothing written; treat non-string values as empty); (c) otherwise `return super().create(request, *args, **kwargs)`. The check order must hold even when a consumer's `get_permissions()` returns something else: the hook runs inside `create`, after DRF's permission step. Do not touch `perform_create`, the serializer, role assignment or the invite flow.
3. **Removal:** delete `RequireStrongSecurity` from `auth/permissions.py` and `require_security_level` from `auth/security.py`; remove imports that become unused only because of the removal; do not alter `SECURITY_ENFORCE_STRONG_AUTH`, `is_level_sufficient`, `get_security_level` or anything else in those modules. If either name is re-exported from a package `__init__` or `__all__`, remove that too (the grep says there is none).
4. **Unchanged:** `invite`/`invite_link` and their permissions, update/destroy rules and the rank helper, `get_queryset`, `RolePolicy` and role changes, signup/reset/recovery actions, the admin auth policy and MFA, who counts as a users admin.

### Tests (new file `tests/test_user_create_permission.py`; existing tests stay green)

Same pattern as the files named above: a viewset subclass with a minimal serializer over native `User` fields (`id`, `username`, `email`, `first_name`) mapped for `{"post": "create"}`, `APIRequestFactory().post("/users/", data, format="json")`, `force_authenticate`, role patch and settings like `tests/test_user_rank_rule.py` (levels 1/2/3, `INVITE_MIN_ROLE_LEVEL = 2`, `SECURITY_LEVELS`/`SECURITY_DEFAULT_LEVEL`), and assert user counts through `get_user_model().objects.count()`. Cases from Part A: authenticated user without admin rights (level 1): valid payload -> 403 and count unchanged; EMPTY payload -> 403 (not 400) and count unchanged; unauthenticated -> refused as today (401/403) and count unchanged; users admin (a level-2 invite admin) and a superuser: create with an e-mail -> 201 and the user exists, create with a username only -> 201, create with an empty payload, with whitespace-only values and with non-string values -> 400 with field errors and count unchanged; a full auth admin whose policy requires `admin_required_auth_factor_count = 2` with a session that is not strong -> 403 and count unchanged (set the policy row and the session level the way `tests/test_auth_policy_cache.py`/`auth/security.py` read them), and with a strong session -> 201; the hook: a subclass whose `can_create_user` returns True lets a level-1 user create (201), one whose `can_create_user` returns False refuses a users admin and a superuser (403, count unchanged); a subclass that overrides `get_permissions()` the way bigler-consult does (returning its own permission class for `create`) and a subclass that overrides `perform_create` the way innoservice does (an extra check raising `PermissionDenied`) still work with a users admin and are still refused for a non-admin; `from django_core_micha.auth.permissions import RequireStrongSecurity` and `from django_core_micha.auth.security import require_security_level` raise `ImportError`; `SECURITY_ENFORCE_STRONG_AUTH` and `is_level_sufficient` still import. Prove every refusal by the unchanged user count.

### Invariants

The generic create is allowed only through `can_create_user` (default: users admins and superusers, request-aware, admin auth policy included) and the check runs before payload validation; a permitted create without username and e-mail is a 400 that writes nothing; the two dead helpers are gone and nothing else in the security modules changes; consumer guards keep working.

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

Source: S246 and S251 (`webapp-management/SECURITY_FINDINGS.md`), requirements round in session
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734` on 2026-10-18. Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. On landing, update the S246 and S251 status lines in the register.
HRAM-DEP-8 carries S247 and targets at least the release that contains this order.
