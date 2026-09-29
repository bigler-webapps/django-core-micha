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

*(to be filled by the Orchestrator: execution directive; estate survey of generic self-PATCH usage; `reviewer` (all lenses) + `sec_reviewer`; release; consumer pin bumps; register rows here and in jg (`JG-SEC-3`).)*
