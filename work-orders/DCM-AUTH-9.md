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

_Filled by the Orchestrator on `git pull`: context package, target working directory, progress contract
and the preamble block. Not dispatchable while this placeholder stands._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: operator decision in the requirements round of session `local_95ff6b21-4aba-46a9-8285-0f1560f0a734`
on 2026-10-17 (DCM-AUTH-5 too strict; admin → admin stays allowed, teacher → teacher not). Report back to:
`local_95ff6b21-4aba-46a9-8285-0f1560f0a734`. Lands after DCM-AUTH-8 (the tier definition depends on it).
On landing, update the S266 and S276 status lines in the register (fixed version and rule).
