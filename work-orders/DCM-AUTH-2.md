# DCM-AUTH-2 — Generic update and delete on a user's own record bypass the self-edit allowlist

# A. Envelope — authored by the Expertenchat

## Goal & expected outcome

- Goal: a user changes their own record only through the path meant for it, and cannot delete their
  own account through a generic route.
- Expected outcome:
  - `PATCH`/`PUT /api/users/<own-id>/` by a non-admin is limited to the same fields as
    `PATCH /api/users/current/` (`current_patch_allowed_fields`), or refused.
  - `DELETE /api/users/<own-id>/` by a non-admin is refused (403). Account deletion, if an app wants
    it, is a separate explicit flow, not a side effect of the generic viewset.

## Context — measured 2026-09-29 against dcm `main` (unchanged since 2.43.5 for this file), via jg-ferien

- `src/django_core_micha/auth/views.py` `BaseUserViewSet`: `get_queryset()` gives a non-admin only
  their own row (`queryset.filter(pk=user.pk)`), so generic `update`/`partial_update`/`destroy` work
  on the own record. `current()` (`url_path="current"`) enforces `current_patch_allowed_fields`
  (first_name, last_name, language, accepted_privacy_statement, accepted_convenience_cookies); the
  generic route does not.
- Consequence: `is_active` is writable on oneself (self-deactivation); `DELETE` deletes one's own
  account. Role and support-agent flags stay protected by `BaseUserSerializer.validate`
  (`RolePolicy.can_change_role`, `can_manage_support_agents`).
- No ucm or jg code uses the generic self-delete: ucm's `deleteUser` is used only by
  `UserListComponent` to delete **other** users, and jg hides deleting oneself there.
- Raised as `JG-SEC-3` (a) in jg-ferien; **operator 2026-09-29: this belongs in dcm.**

## Scope + non-goals

- In scope: restrict generic self-updates to the allowlist (or refuse them, pointing to `current`),
  refuse generic self-delete for non-admins.
- Explicit non-goals / do-not-touch:
  - Admin behaviour unchanged (admins editing/deleting others).
  - `current()` unchanged.
  - Apps' own overrides (e.g. jg's `get_queryset`) are their business; this is the base behaviour.

## Tier · gates

- **Tier 3** — auth/permissions inside shared-core.
- Reviewer: `reviewer` (all lenses) · `sec_reviewer`.
- **Done means released and every consumer's pin bumped** after checking none relies on the generic
  self-path (estate survey in the WO's implementation map).

## Risks

- A consumer app may PATCH the own record through the generic route today; survey the estate's
  frontends for `PATCH /api/users/<id>/` on the current user before changing behaviour, and list the
  result.

## Required tests to WRITE (you write them and run YOUR OWN new ones; the orchestrator's run is the gate)

1. Non-admin `PATCH /users/<own-id>/` with `is_active: false` → refused or ignored; with `first_name`
   → allowed (or refused with a pointer to `current`, per the chosen shape).
2. Non-admin `DELETE /users/<own-id>/` → 403, user still exists.
3. Admin `DELETE /users/<other-id>/` → unchanged.
4. `current()` behaviour unchanged.

---

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

*(placeholder — no context package yet, no progress contract yet; the Orchestrator fills this
part on `git pull` and appends the preamble below to the invocation. Do not dispatch on this
placeholder.)*

## Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `main`)

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

## Execution record

Implemented directly by the Orchestrator (Claude), not dispatched to Codex — a small, well-scoped
view-level change; author == Orchestrator, so the independent review below is what makes this a
Tier-3-compliant commit.

### Estate survey (generic self-PATCH/PUT/DELETE on `/api/users/<id>/`, per-repo scoped)

- `ui-core-micha`'s shared `authApi.jsx`: `deleteUser`/`updateUserRole`/`updateUserSupportStatus` are
  used only on OTHER users' rows by admins (role/support-agent management), never on the caller's own
  id.
- **jg-ferien** has its own local `usersApi.js` duplicating these calls. `ListSection.jsx` excludes
  self from the deletable/role-editable row set (`user.id === currentUser.id → false`) — no generic
  self-delete there. `ParticipantDetails.jsx` calls `patchUser(r.user.id, {first_name, last_name})`,
  which CAN target the editor's own row (a non-admin registered as a participant in their own event)
  — both fields are already in `current_patch_allowed_fields`, so this flow is unaffected by either
  restriction shape.
- **Photogallery, bigler-consult, fitness-monitor, musiknoten, survey_app, survey_contact_app,
  cockpit, webshop-guenter** share a `UserListTab.jsx` pattern that also excludes self from the
  delete/role-manage row set (confirmed in Photogallery: `rowUser.id === currentUser.id → false`).
- **kerzenziehen** (`UsersCard.jsx`) and **reimbursements** (`UserListTab.jsx`) do NOT exclude self
  from the admin delete button (kerzenziehen only excludes superusers) — an admin could today
  self-delete or, in kerzenziehen's case, self-role-change via the generic route. This is exactly the
  exposure this WO closes; fixing it changes no relied-upon behaviour (nothing currently depends on
  that self-delete succeeding).
- **Kira**: no source-level usage — the pattern only matched compiled `frontend/build*/` bundles
  (ucm baked in), not separate app code.
- **survey_app**'s `AllowedProjectSitesDialog.jsx` hits a custom `@action` route
  (`/allowed-project-sites/`), not the generic update — unaffected by this change.
- **Gustav, cinevia, hram, innoservice, spesix, yopoulab-hhs-2026**: no match for the surveyed
  patterns.

**Design choice** (per the Envelope's "limited to the allowlist, or refused" alternatives): enforce
the SAME `current_patch_allowed_fields` allowlist on the generic route for a non-admin self-target,
rather than blanket refusal — blanket refusal would have broken jg-ferien's `ParticipantDetails.jsx`
flow above. `DELETE` is refused outright (no legitimate self-delete use found anywhere surveyed).

### Review — Tier 3, all `reviewer` lenses + `sec_reviewer`

Runtime/model: `codex`/`gpt-5.6-luna` for all 5 lenses (per `.claude/models.local.json`), dispatched
concurrently on the same diff (view.py + new test file), each with its lens brief pasted inline.
`.claude/codex-status.md`: 2026-09-29 `available` (no probe needed).

Raised (deduplicated) · accepted · fixed, before the landing commit:
- **[defect] `envelope`/`regression` (regression lens; independently caught in the Orchestrator's own
  pass too):** the allowlist/permission check ran BEFORE `get_object()`, so it fired unconditionally
  for ANY non-admin call — turning the pre-existing 404 (a non-admin's queryset already excludes
  another user's row) into 400 (disallowed field) or 403 (delete), for an id that isn't theirs.
  **Fixed:** `update()`/`destroy()` now call `self.get_object()` first; the new logic only runs once
  the object is visible to the caller, which for a non-admin is only ever their own row. Added
  regression tests for both routes against another user's id (still 404).
- **[defect] `sec_reviewer` (#4) + `tests` lens (PUT untested):** a non-admin `PUT` (full replace,
  `partial=False`) with only allowlisted fields could fail required-field validation or, with a
  different serializer, reset omitted fields to defaults — a different exposure shape than `PATCH`.
  **Fixed:** `update()` now forces `partial=True` for a non-admin target, so `PUT` behaves like
  `current()` (only submitted, allowlisted fields change). Added PUT-specific tests (allowlisted
  field succeeds without a full payload; `is_active` still rejected).
- **[nit] `envelope` lens:** the "`current()` unchanged" test only exercised PATCH, not GET. Added a
  GET assertion.

Rejected (informational / out of scope, no code change):
- `sec_reviewer` (#1): asked whether `can_view_users_admin` could misalign with some other
  "can-view-full-queryset" path in a consumer app. No such path found in the base viewset; this is
  the SAME predicate `get_queryset()` itself already gates on, so the two cannot diverge here. A
  consumer-app-specific audit of that predicate is a separate concern, not this WO's.
- `sec_reviewer` (#2, #3): explicitly checked timing/enumeration and a non-admin-to-other-user
  mutation path; found no issue (also mooted by the get_object()-first fix above).
- `duplication` lens: no capability-level duplication found.
- `tests` lens: noted the admin-path tests are compatibility guards (expected — they pin "unchanged
  admin behaviour", not new logic) and that no non-superuser role-based admin is exercised; the
  Envelope's own required tests (items 3–4) only ask for "admin", which the superuser fixture
  satisfies — a broader role-matrix is a nice-to-have, not this WO's scope.

Worst accepted: defect (two: the status-code regression, and the PUT full-replace edge case; both
fixed before commit).

### Tests (affected-areas set, per AGENTS.md Test scope — not the full suite)

`PYTHONUTF8=1 PYTHONPATH=src pytest tests/ -k "auth or permission or invite or reset or throttle or
role or user"` → 111 passed (109 pre-existing + the 10 new in
`tests/test_self_edit_delete_restriction.py`), re-run clean after the fixes above.

### Release

Patch bump (existing self-edit/self-delete surface hardened, no new capability):
**django-core-micha 2.44.1**, CHANGELOG entry + `pyproject.toml` bump, `publish.yml` triggers on this
push to `main` (path filter matches `pyproject.toml` + `src/django_core_micha/**`).

### Consumer pin bumps — explicitly deferred, per operator instruction (2026-09-29)

The operator's handover for this WO explicitly excluded bumping any consumer's `django-core-micha`
pin from this session's scope: **jg-ferien** inherits this fix in its `UserViewSet` and its Paket 1
(`JG-SEC-2`/`JG-SEC-3`) is currently in flight there — the pin bump belongs AFTER that package lands,
as its own WO, so Paket 1's tests get re-run against the new pin rather than racing it. This narrows
the Envelope's own stated Tier-3 gate ("Done means released and every consumer's pin bumped") for
THIS WO to "released"; the pin-bump follow-up is tracked as a pointer here, not a blocking item on
this row. No other consumer app was identified as depending on the fixed behaviour (see estate survey
above), so no other pin bump is time-sensitive.

A corresponding update to jg-ferien's own `WORK_ORDERS.md` (`JG-SEC-3` (a) → resolved upstream in dcm)
is jg-ferien's repo, out of this session's scope (this handover named django-core-micha only) — flagged
here for whoever next opens that repo's register.
