# DCM-AUTH-5 — Rank check for invite links, user edits and deletes (closes S266, S276)

# A. Envelope

**Goal.** Close S266 and S276 from the 2026-10-17 estate audit (`webapp-management/SECURITY_FINDINGS.md`).
Expected outcome: an invite admin can no longer take over, deactivate, edit or delete accounts that rank
equal to or above them; invite links are no longer a password-reset path for existing accounts.

**Background (from the findings).**
- S266: `InviteActionsMixin.invite_link` (`invitations/mixins.py`) is gated only by `IsInviteAdminOrSuperuser`,
  takes any user from `BaseUserViewSet.get_queryset` (all users for invite admins) and returns an
  `/invite/<uid>/<token>/` link that `PasswordResetConfirmView` accepts to set a password. With
  `INVITE_MIN_ROLE_LEVEL`/`INVITE_ADMIN_ROLES` below the top role (survey_app/survey_contact_app supervisors,
  hram teachers S309, kerzenziehen team S370, cockpit, fitness-monitor, hpc-bridge, maana, musiknoten,
  innoservice), a level-2 user takes over admin and superuser accounts.
- S276: `BaseUserViewSet.update`/`destroy` (`auth/views.py`) restrict only the non-admin self-edit case;
  `is_active` is writable; invite admins can deactivate or delete superiors.
- Role changes already follow `RolePolicy` and stay as they are.

**Scope (operator decisions 2026-10-17).**
1. **Rank rule.** An actor may act on another user only if the actor is a superuser, or the actor's role
   level is strictly greater than the target's level and the target is not a superuser. Equal levels are
   refused. One shared helper, used by every path below.
2. **Paths covered.** `invite_link`; `update`/`partial_update` when the target is not the actor; `destroy`
   when the target is not the actor. A refused request returns 403 (or 404 where the target is already
   outside the queryset) and changes nothing.
3. **Invite links only for new accounts.** `invite_link` additionally requires that the target has never
   logged in (`last_login` is empty). For any other account it is refused with an error code that points
   the admin to the self-service password reset (the owner receives the mail).
4. **Unchanged.** Self-edit rules (DCM-AUTH-2/3), `RolePolicy` role changes, `list`/`retrieve` and queryset
   scoping, the self-service reset flow, superuser abilities.
5. **Release.** Minor version bump with a CHANGELOG entry that states the behaviour change for consumers
   (invite admins lose the ability to act on equal or higher ranks; invite links only for never-logged-in
   accounts).

**Non-goals / do not touch.** App pin bumps (separate per-app work orders after release); what invite admins
may *see* (`get_queryset`, S309's user listing); admin MFA (S267); social login (DCM-AUTH-6); S246 (`create`).

**Risks.** Apps whose invite admins are the top role (e.g. bigler-consult owners, reimbursements admins) can
no longer manage peers of the same level — only superusers can; apps without `ROLE_DEFINITIONS` (everyone
level 1) are unaffected because only superusers are invite admins there. Consumers that call `super()` from
overridden `update`/`destroy` inherit the rule — intended.

**Required tests (write as part of the implementation).**
- Level-2 actor → level-3 target: `invite_link`, `PATCH is_active`, `PATCH` other fields, `DELETE` → refused,
  target unchanged.
- Level-2 actor → superuser target: all four refused. Equal level → refused.
- Level-3 actor → level-2 target: allowed (with `invite_link` only if the target never logged in).
- `invite_link` on a target with `last_login` set → refused with the reset error code, also when the actor
  is a superuser (the "never logged in" rule applies to everyone).
- Superuser → any non-superuser: update and destroy allowed.
- Existing DCM-AUTH-2/3/4 self-edit tests and invite-lifetime tests stay green.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (change inside shared core, authorization).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and the one interpretation that keeps the existing tests green. One dispatch; complete, no stubs. This is an authorization change in shared core: when in doubt, refuse.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's). Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/auth/permissions.py`: imports `get_role_level_for_user` from `auth.roles` and defines the request-aware wrappers; `can_view_users_admin(user, request=None)` is `has_invite_admin_rights(user, request=request)` (the Users-tab/invite admin check, includes the admin-auth-policy check). `auth/access.py` holds `has_invite_admin_rights(user)` (superuser, full auth admin, `INVITE_ADMIN_ROLES`, `INVITE_MIN_ROLE_LEVEL` default level 2). `auth/roles.py`: `get_role_code_for_user(user)` (profile role or default), `get_role_level_for_code`, `get_role_level_for_user(user)` (calls `get_role_code_for_user` through the module namespace).
- `src/django_core_micha/auth/views.py`, class `BaseUserViewSet` (queryset: all users for `can_view_users_admin`, else the requester's own row): `update(self, request, *args, **kwargs)` (about line 324; `partial_update` calls it) resolves `instance = self.get_object()`, computes `is_self_edit = instance.pk == request.user.pk`, applies the self-edit allowlist for non-admin self-edits and then `super().update(...)`; `destroy` (about line 352) does the same for DELETE. `update_role` (about line 693) is the role-change action and stays as it is.
- `src/django_core_micha/invitations/mixins.py`, `InviteActionsMixin.invite_link` (about line 186; `permission_classes=[IsAuthenticated, IsInviteAdminOrSuperuser]`): `user = self.get_object(); url = self._build_frontend_url(request, user, is_new_user=True)`; response `{"invite_link": url}`.
- Existing tests to keep green and to model on: `tests/test_self_edit_delete_restriction.py` (note its `_UserViewSet`, `_WideningUserViewSet`, `_authed(...)`, the autouse settings fixture with `ROLE_DEFINITIONS` user=1 / admin=3, the minimal serializer, and the superuser `admin` fixture), `tests/test_invite_link_lifetime.py`. The test settings use the bare `django.contrib.auth` User without a profile: to give users roles in the tests, monkeypatch `django_core_micha.auth.roles.get_role_code_for_user` with a function that maps the user (for example by username or a dict keyed by pk) to a role code; every module reaches the level through that function, so one patch is enough.

### Fixed design

1. **One shared helper** in `auth/permissions.py`: `can_act_on_user(actor, target) -> bool`. True when the actor is a superuser; False when the target is a superuser; otherwise True only when `get_role_level_for_user(actor) > get_role_level_for_user(target)` (strictly greater; equal is refused). It does not look at `request`, never raises for a user without a profile (that is the default role), and treats an unauthenticated actor as False.
2. **Where it applies — interpretation that keeps the DCM-AUTH-2/3 behaviour:** the rank rule guards the power that the users/invite admin gate gives. In `update` and `destroy`, after `instance = self.get_object()` and only when `instance.pk != request.user.pk`: if `can_view_users_admin(request.user, request=request)` is true and `can_act_on_user(request.user, instance)` is false, raise `rest_framework.exceptions.PermissionDenied` (403, message without any detail about the target) BEFORE anything is changed or `super()` is called. An actor who is NOT a users admin and reaches other rows only through a consumer's widened queryset (jg-ferien org admins and department teams, DCM-AUTH-3) is not subject to this rule: their access is governed by the consumer's own object permissions and the existing `test_non_admin_widened_queryset_*` tests must stay green unchanged. Self targets (`instance.pk == request.user.pk`) keep exactly today's behaviour. 404 for rows outside the queryset stays (the `get_object()` comes first).
3. **`invite_link`:** after `self.get_object()` (404 stays) and for every actor (the permission class already limits it to invite admins): (a) rank check first: `can_act_on_user(request.user, user)` false (for a non-superuser actor this also covers a link for themselves) -> `Response({"code": "Auth.RANK_INSUFFICIENT"}, status=403)`; (b) then the never-logged-in rule — `user.last_login` not empty -> `Response({"code": "Auth.USE_PASSWORD_RESET"}, status=409)` for EVERYONE including superusers (the admin is meant to use the self-service reset, the owner receives the mail). The rank check comes before the last_login check so a low-ranked admin cannot probe who has logged in. Only a refused request returns these codes; a successful one is unchanged (`{"invite_link": url}`).
4. **Unchanged:** `get_queryset`, `list`/`retrieve`, `create`/`invite`, self-edit allowlist, `RolePolicy` and `update_role`, the self-service `reset_request`, token generators and link building, `BaseUserSerializer`. No new setting, no model or migration change.

### Tests (new file `tests/test_user_rank_rule.py`; existing tests stay green)

Use the pattern of `tests/test_self_edit_delete_restriction.py` (viewset subclass with a minimal serializer, `APIRequestFactory`, `force_authenticate`) plus the role patch described above. Roles: `ROLE_DEFINITIONS` with levels 1, 2, 3 and `INVITE_MIN_ROLE_LEVEL=2`, so level 2 and 3 are invite admins (set `SECURITY_LEVELS`/`SECURITY_DEFAULT_LEVEL` like the existing fixture; make sure the admin-auth-policy does not interfere). Cases from Part A: level-2 actor on a level-3 target: `invite_link`, `PATCH is_active`, `PATCH first_name`, `PUT`, `DELETE` all refused with 403 and the target unchanged (re-read from the database); level-2 on superuser: all refused; level-2 on level-2 (equal): refused; level-3 on level-2: allowed (`invite_link` only when the target never logged in; also check a target with `last_login` set gets 409 `Auth.USE_PASSWORD_RESET` from a level-3 actor and from a superuser); superuser on a non-superuser: `PATCH`, `PUT` and `DELETE` allowed, and `invite_link` allowed for a never-logged-in target; the codes (`Auth.RANK_INSUFFICIENT` 403 before `Auth.USE_PASSWORD_RESET` 409: a refused-rank request on a logged-in target returns 403, not 409); a level-2 actor patching their OWN row behaves as before; a non-admin reaching another row through a widened queryset behaves as before (reuse the existing widened-view pattern); an unauthenticated request is refused as today; `can_act_on_user` unit cases (superuser actor, superuser target, greater, equal, lower, user without profile, unauthenticated). Prove each refusal leaves the row unchanged and the user not deleted.

### Invariants

The rank rule uses one helper everywhere; equal rank is refused; a superuser target can only be touched by a superuser; a refused request changes nothing and reveals nothing about the target beyond the error code; invite links are only for never-logged-in accounts; non-admin widened-queryset behaviour and self-edit behaviour are byte-for-byte the same as before.

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

Source: night audit 2026-10-17, S266/S276 and app confirmations S309, S370; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
