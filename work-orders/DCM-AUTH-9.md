# DCM-AUTH-9 — Rank rule: the admin tier may act on equal rank (adjusts DCM-AUTH-5; S266, S276)

# A. Envelope

**Goal.** Adjust the DCM-AUTH-5 rank rule shipped in 3.9.0, which the operator judged too strict. Expected
outcome: an actor in the admin tier may administer accounts of equal rank again (edit, deactivate, delete,
and an invite link under the never-logged-in rule); every other actor must strictly outrank the target.
Admin → admin is allowed, teacher → teacher and supervisor → supervisor stay refused, nobody but a
superuser touches a superuser, nobody touches a higher rank.

**Background.** `can_act_on_user(actor, target)` in `auth/permissions.py` admits superusers, refuses
superuser targets and otherwise requires the actor's level to be strictly greater than the target's. It
guards `BaseUserViewSet.update`/`partial_update`/`destroy` for foreign targets and
`InviteActionsMixin.invite_link`. The attack path behind S266 and S276, a level-2 invite admin taking over
or locking out an admin or superuser account, stays closed under the adjusted rule.

**Scope (operator decisions 2026-10-17).**
1. **Rule.** A superuser may act on anyone; nobody else on a superuser. Otherwise the actor may act when
   its level is greater than the target's, or equal to it and the actor holds full auth-admin rights
   (`has_full_auth_admin_rights`: level at or above `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL` or its computed
   default, after DCM-AUTH-8). That is the admin tier. Invite admins below it (teachers, team, supervisors)
   keep the strict rule.
2. **Same helper, same paths** as DCM-AUTH-5; error codes unchanged (403, `Auth.RANK_INSUFFICIENT` on the
   invite link).
3. **Invite links stay limited to accounts that never logged in** (409 `Auth.USE_PASSWORD_RESET`), for
   everyone including superusers and the admin tier. An actor's own account therefore never gets an
   invite link through this path.
4. **Unchanged.** Self-edit rules (DCM-AUTH-2/3), `RolePolicy` and role changes, queryset scoping, the
   self-service reset, superuser abilities.
5. **Release.** Minor version bump with a CHANGELOG entry under Security that states the change relative
   to 3.9.0: the admin tier can again manage peers of equal rank; invite admins below it cannot.

**Non-goals / do not touch.** App pins; role changes (`update_role`); who can see whom (`get_queryset`);
the auth-policy threshold itself (DCM-AUTH-8); admin MFA (DCM-AUTH-10).

**Risks.** Where an app sets `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL` below its top level, every role at or
above that threshold is admin tier and may act on equals; the app chose that threshold. Roles that tie at
the admin tier (spesix `admin` and `traeger_admin`, S345) may act on each other; the role-change side of
S345 stays with spesix's own order. Consumers that call `super()` from overridden `update`/`destroy`
inherit the adjusted rule.

**Required tests (write as part of the implementation; adapt `tests/test_user_rank_rule.py`, keep its
pattern).**
- Admin tier (level 3, threshold 3) → another level-3 account: `PATCH` other fields, `PATCH is_active`
  and `DELETE` allowed and effective; invite link allowed when the target never logged in, 409 when it did.
- Admin tier → superuser: all refused, target unchanged. Admin tier → higher level (levels 1, 3, 4 with
  threshold 3; actor 3, target 4): refused.
- Level-2 invite admin (`INVITE_MIN_ROLE_LEVEL = 2`, below the tier) → level-2 peer: all refused as in
  3.9.0; → level 1: allowed.
- Level-3 actor in a 1/3 hierarchy with an explicit threshold of 4 → level-3 peer: refused. The tier
  decides, not the level alone.
- Superuser → any account: allowed (existing). Unauthenticated actor: refused (existing).
- `test_can_act_on_user_rank_rules` and the self-link test adapted to the rule; the DCM-AUTH-2/3 self-edit
  tests and the widened-queryset test stay green.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (change inside shared core, authorization).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the exact rule and the tests to adapt. One dispatch; complete, no stubs. This is an authorization change in shared core: when in doubt, refuse.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's), `tests/settings.py`, and the files `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` (another session's uncommitted work: leave them exactly as they are). Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/auth/permissions.py`: `can_act_on_user(actor, target)` (the only function to change): False for an unauthenticated actor, True for a superuser actor, False for a missing or superuser target, otherwise `get_role_level_for_user(actor) > get_role_level_for_user(target)`. `get_role_level_for_user` is imported there from `auth.roles`. The request-aware wrapper `has_full_auth_admin_rights(user, request=None)` in the same file adds the admin auth policy check and is NOT what this rule uses.
- `src/django_core_micha/auth/access.py`: `has_full_auth_admin_rights(user)` (the base function, `_has_min_level(user, "AUTH_POLICY_WRITE_MIN_ROLE_LEVEL", _default_auth_policy_write_level())`: True for superusers and for roles at or above the explicit threshold or its computed default; DCM-AUTH-8 just made the computed default sit above the level of a role-less user and at least `ROLE_LEVEL_3` for flat setups). Import this BASE function into `permissions.py` under an alias that does not collide with the wrapper of the same name (for example `from django_core_micha.auth.access import has_full_auth_admin_rights as base_has_full_auth_admin_rights`; the file already imports other base functions with a `base_` alias, follow that pattern).
- Callers of `can_act_on_user` (unchanged, they get the new rule): `auth/views.py` `BaseUserViewSet.update`/`partial_update`/`destroy` (foreign targets, users admins only; refused with `PermissionDenied`) and `invitations/mixins.py` `InviteActionsMixin.invite_link` (403 `Auth.RANK_INSUFFICIENT` before the never-logged-in 409 `Auth.USE_PASSWORD_RESET`). Do not change them.
- `tests/test_user_rank_rule.py` (read it fully): fixtures set `ROLE_DEFINITIONS` user=1 / manager=2 / senior=3, `INVITE_MIN_ROLE_LEVEL = 2`, patch `django_core_micha.auth.roles.get_role_code_for_user` through a `role_codes` dict, build users with a `users(username, role, superuser=, last_login=)` factory, drive `BaseUserViewSet` through `_call_crud(method, actor, target, data)` and the invite link through `_call_invite(actor, target)`. With these definitions the admin tier (after DCM-AUTH-8) is level 3 (the highest level above the default role's level 1).

### Fixed design

1. **Rule** in `can_act_on_user(actor, target)`, in this order: unauthenticated actor -> False; superuser actor -> True; missing target or superuser target -> False; `actor_level > target_level` -> True; `actor_level == target_level` and the actor holds full auth-admin rights through the BASE function `has_full_auth_admin_rights(actor)` -> True; otherwise False. A lower actor level is always False. Equal level without the admin tier stays False (teachers, supervisors, team members). The tier decides, not the level alone: a level-3 actor in a 1/3 hierarchy with an explicit `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 4` is not admin tier and is refused on a level-3 peer.
2. **Same helper, same callers, same error codes.** No change in `views.py` or `mixins.py`. The invite link still requires `last_login` to be empty for everybody (409 `Auth.USE_PASSWORD_RESET`), so an actor never gets an invite link for their own (logged-in) account through this path; keep the rank-before-logged-in order.
3. **Update the docstring** of `can_act_on_user` to describe the rule (superuser; never a superuser target; greater level; equal level only for the admin tier).
4. **Unchanged:** self-edit rules (DCM-AUTH-2/3), the widened-queryset behaviour for non-admins, `RolePolicy` and role changes, `get_queryset`, superuser abilities.

### Tests (adapt `tests/test_user_rank_rule.py`, keep its pattern; keep the DCM-AUTH-2/3 and widened-queryset tests green unchanged)

Adapt the assertions that encoded the old strict rule and add the cases of Part A: admin tier (level 3, threshold 3) -> another level-3 account: `PATCH` other fields, `PATCH is_active` and `DELETE` allowed and effective (re-read from the database), `invite_link` allowed when the target never logged in and 409 `Auth.USE_PASSWORD_RESET` when it did; admin tier -> superuser: all refused, target unchanged; admin tier -> higher level (levels 1, 3, 4, explicit threshold 3: actor level 3, target level 4): refused, target unchanged; level-2 invite admin (`INVITE_MIN_ROLE_LEVEL = 2`, below the tier) -> level-2 peer: all refused as in 3.9.0, and -> level 1: allowed; level-3 actor in a 1/3 hierarchy with an explicit threshold of 4 -> level-3 peer: refused (the tier decides); superuser -> any account: allowed; unauthenticated actor: refused; `can_act_on_user` unit cases for all combinations (superuser actor, superuser target, greater, equal with and without the admin tier, lower, missing target, unauthenticated); an admin-tier actor's own invite link is refused with the logged-in 409 when the actor has logged in and refused with 403 `Auth.RANK_INSUFFICIENT` never by this rule for a superuser actor (superuser -> self: allowed by rank, then 409 if logged in); update `test_can_act_on_user_rank_rules`, `test_the_rank_guard_is_what_decides_between_equal_and_lower_targets` (senior -> equal senior is now allowed: make the equal-refusal case a manager -> manager one) and `test_non_superuser_cannot_get_an_invite_link_for_themselves` (a level-3 admin-tier actor with no `last_login` now passes the rank rule for their own account: assert the outcome of the rule, which is a link only if the account never logged in; the test must say that a normal logged-in actor gets 409). Every refusal must leave the target row unchanged.

### Invariants

A superuser acts on anyone and nobody else on a superuser; a strictly higher level always acts; an equal level acts only for the admin tier (base `has_full_auth_admin_rights`); nobody acts on a higher level; invite links only for accounts that never logged in; self-edit, widened-queryset and role-change behaviour unchanged.

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
on 2026-10-17 (DCM-AUTH-5 too strict; admin → admin stays allowed, teacher → teacher not). Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. Lands after DCM-AUTH-8 (the tier definition depends on it).
On landing, update the S266 and S276 status lines in the register (fixed version and rule).
