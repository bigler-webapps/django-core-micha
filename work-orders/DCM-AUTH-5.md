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

_To be filled by the Orchestrator (context package, preamble, progress contract) before dispatch._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S266/S276 and app confirmations S309, S370; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
