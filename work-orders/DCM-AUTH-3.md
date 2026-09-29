# DCM-AUTH-3 — The DCM-AUTH-2 gate also hits a non-admin's writes on OTHER users

# A. Envelope — authored by the Expertenchat

## Goal & expected outcome

- Goal: the self-edit restriction from `DCM-AUTH-2` applies to exactly what it was written for, a
  non-admin writing **their own** record through the generic routes. It must not touch writes on
  other users that a consuming app deliberately lets a non-admin make.
- Expected outcome:
  - `PATCH`/`PUT /api/users/<id>/` by a non-admin: the allowlist and the forced partial semantics
    apply **only when `<id>` is the requesting user**. For any other user that the consumer's
    `get_queryset()` exposes, behaviour is exactly as before 2.44.1.
  - `DELETE /api/users/<id>/` by a non-admin: refused (403) **only when `<id>` is the requesting
    user**. For another exposed user, behaviour is exactly as before 2.44.1.
  - All `DCM-AUTH-2` guarantees for the self-target stay, including the pre-existing 404 on ids
    outside the queryset.
  - Released as a patch (2.44.2).

## Context — measured 2026-09-29 against `main` at `f4c6a9e` (2.44.1)

- `src/django_core_micha/auth/views.py` `BaseUserViewSet.update()` (~l.324) and `destroy()`
  (~l.349), both added in `c9a4516`:
  ```python
  self.get_object()
  if not can_view_users_admin(request.user, request=request):
      self._enforce_safe_profile_fields(request)
      kwargs["partial"] = True
  ```
  and in `destroy()` `raise PermissionDenied("Cannot delete your own account through this endpoint.")`.
  Neither compares the resolved object with `request.user`.
- The code comment states the assumption the gate relies on: "a non-admin's queryset only ever
  contains their own row". That holds for the **base** `get_queryset()`, not for consumers that
  override it.
- **jg-ferien breaks the assumption on purpose.** Its `UserViewSet(BaseUserViewSet)`
  (`jg-ferien/backend/users/views.py`, `get_queryset` ~l.189) returns, quoted in shape:
  ```python
  if is_admin_user(user): return all users
  if has_organization_admin_role(user): return all users        # S93: org admins see all
  if user has active department memberships with role "team":
      return users in those departments / organizations
  return queryset                                                # base: own row
  ```
  Org admins and department-team members in jg carry the **global** role `none` (measured locally
  2026-09-29 on accounts created through the app's own flows: `has_invite_admin_rights` False for
  both). So on 2.44.1 a jg org admin editing a user of their organization is cut down to the self-edit
  allowlist, and deleting one gets 403 with the message "Cannot delete your own account" for someone
  else's account. jg's `JG-SEC-2` (landed on jg `develop` as `0972afe`) deliberately grants org
  admins these writes on users of their own organizations.
- jg's local tests mount this repo's checkout, so they already run against 2.44.1. jg's test
  `backend/users/test_user_viewset_object_permissions.py` (~l.70) had to set `profile.role = "admin"`
  on its org admin to get past this gate. That workaround is the symptom.
- No consumer runs 2.44.1 today: every app pins `django-core-micha==` exactly, all on 2.44.0 or older.
- Other overrides checked: `reimbursements` narrows a non-admin to their own row (unaffected);
  `innoservice` does not widen the queryset (unaffected); the other consumers do not override
  `get_queryset`.

## Scope + non-goals

- In scope:
  - Gate both checks on "the resolved object is the requesting user" (compare primary keys of the
    object `get_object()` returns and `request.user`), keeping the object-first ordering.
  - Correct the code comments so they no longer claim the queryset invariant.
  - CHANGELOG entry and the patch release 2.44.2.
- Explicit non-goals / do-not-touch:
  - `current()` unchanged; admin behaviour unchanged; the allowlist contents unchanged.
  - Whether an **admin** may delete their own account through the generic route is a separate item
    (`DCM-AUTH-4`), not this WO.
  - No consumer pin bumps.

## Tier · gates

- **Tier 3** — auth inside shared-core.
- Precondition: none.

## Risks

- A test that only uses the base `get_queryset()` cannot see this defect, because there a non-admin
  never reaches another user's row. The new tests need a viewset whose `get_queryset()` exposes other
  users to a non-admin, the shape jg uses.
- The comparison must use the object the viewset resolved, not the raw URL kwarg: a lookup field
  other than `pk` would otherwise compare the wrong thing.

## Required tests to WRITE (you write them and run YOUR OWN new ones; the orchestrator's run is the gate)

In `tests/test_self_edit_delete_restriction.py`, with an additional test viewset whose
`get_queryset()` returns all users to a non-admin:

1. Non-admin `PATCH` of **another** user with a field outside the allowlist (e.g. `is_active`) → not
   rejected by this gate (behaves as on 2.44.0).
2. Non-admin `DELETE` of **another** user → not refused by this gate (behaves as on 2.44.0).
3. Same viewset, **self**-target: `PATCH is_active` → 400; allowlisted field → 200; `PUT` partial;
   `DELETE` → 403.
4. The existing `DCM-AUTH-2` tests stay green unchanged, including the 404 cases.
5. **Mutation proof:** drop the self comparison → tests 1 and 2 fail.

---

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

*(placeholder — no context package yet, no progress contract yet; the Orchestrator fills this
part on `git pull` and appends the preamble below to the invocation. Do not dispatch on this
placeholder.)*

## Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `main`).

## Preamble — a REQUIRED block IN this file, not something appended at invocation

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

---

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.**

*(to be filled by the Orchestrator: execution directive; `reviewer` (all lenses) + `sec_reviewer`;
affected-areas set as for DCM-AUTH-2; release 2.44.2; register Notiz names this as
`regression of c9a4516 (reviewed by codex/gpt-5.6-luna, 4 lenses + sec_reviewer, passed with 3
fixes)`; register stays `released` until consumers bump.)*
