# DCM-PERF-2 — Missing indexes on the notification feed's hot query path

## Part A — Envelope

**Goal.** `NotificationRecipient` and `Notification` lack indexes matching the actual query shape
of the two hottest notification endpoints (`CanonicalInboxView`, `CanonicalUnreadCountView`). Add
the indexes that match those queries.

**Expected outcome.** `GET /api/notifications/feed/` and `GET /api/notifications/feed/unread-count/`
drop from the ~850–990 ms they consistently measure today to something proportionate to their tiny
payloads (a few hundred bytes to ~1 KB), verified with a real `EXPLAIN ANALYZE` before/after, not
just a wall-clock feel.

**Why.** Surfaced from a jg-ferien staging network capture (2026-08-29): across two independent page
loads, these two endpoints were consistently the two slowest calls on the page (854–992 ms), while
15+ sibling concurrent requests on the same page ranged 150–500 ms. That consistency is the tell —
generic request-queuing (the subject of jg-ferien's separate `JG-PERF-8`) would jumble the ranking
run to run, not reliably reproduce the same two endpoints as the two slowest twice.

**Root cause, verified at the source (not inferred):**
- `NotificationRecipient.Meta.indexes` (`notifications/models.py:279-281`) declares exactly one
  index: `["user", "done_at"]`. Both hot endpoints filter on `user` + `seen_at` + `dismissed_at`
  (`CanonicalUnreadCountView.get`, `notifications/views.py:217-225`; `CanonicalInboxView`'s
  `status=unseen|active` branches, `:178-184`) — none of which this index covers. Every request
  scans that user's full recipient history unaided.
- `Notification.created_at` (`notifications/models.py:228`) has no index at all, but
  `CanonicalInboxView.get_queryset()` orders by `-notification__created_at`
  (`notifications/views.py:176`) — an unindexed sort, more expensive as the table grows.
  `notification_type` and `category` (`:212-213`) already carry `db_index=True` and are not the
  bottleneck here.

### Scope

1. `NotificationRecipient`: add a composite index covering the actual filter columns —
   `models.Index(fields=["user", "seen_at", "dismissed_at"])`. Keep the existing `["user",
   "done_at"]` index (still used by `done`-status filtering and `count_active_todos_for_user`'s
   query shape) — this is additive, not a replacement.
2. `Notification`: add `db_index=True` to `created_at`, matching the existing style on
   `notification_type`/`category` in the same model (a single-field index, not a new
   `Meta.indexes` list, for consistency with how this model already declares its indexes).
3. Migration `0010_...` (next free number in `notifications/migrations/`, confirm against the
   live directory at implementation time).

### Non-goals / do not touch

- No change to either view's query logic, filtering semantics, or response shape — this is
  purely an index addition underneath unchanged queries.
- No change to `count_active_todos_for_user`/`_materialized_candidates`/the todo subsystem — for
  apps with no registered todo providers (jg-ferien included) that path already short-circuits
  cheaply; it is not part of the measured slowness and is out of scope.
- No change to `iter_feed_hidden_type_keys()` or the in-memory type registry — already O(registry
  size), not a DB cost.
- Do not touch `JG-PERF-8` (jg-ferien's separate worker-process fix) — different repo, different
  root cause, independently valuable; do not conflate the two in review or in this WO's tests.

### Tier

**Tier 3 — shared-core** (a change inside `django-core-micha` is Tier 3 regardless of how additive
the change looks, per the estate's own tiering rule) **and** schema/migration, doubly sensitive by
the same table. `reviewer` (query-plan verification, migration correctness) — no `sec_reviewer`
(no auth/permission surface touched) — no `ui_reviewer` (no frontend).

### Required tests to WRITE

1. `EXPLAIN ANALYZE` (or Django's `django.test.utils.CaptureQueriesContext` + a query-plan check,
   whichever this repo's existing perf tests use as precedent — check `notifications/tests/` and
   `auth/tests/test_auth_policy_cache.py` for the established pattern first) confirming the new
   indexes are actually used by both hot queries — not just that they exist, but that Postgres's
   planner picks them for the exact `CanonicalInboxView`/`CanonicalUnreadCountView` query shapes.
2. Regression: existing `notifications/tests/test_canonical_notification_api.py` stays green
   unchanged — same filtering behavior, same response shape, only the index layer changes.
3. Migration applies cleanly forward and backward (`migrate notifications 0009` then back to
   `0010`) on a representative dataset size, not just an empty test DB — an index migration that
   works instantly on zero rows can still lock or take meaningfully long on a populated table;
   note (not necessarily fix) any lock-duration concern for the Orchestrator's deploy-timing
   awareness.

---

## Part B — Implementation map — ADDRESSED TO THE IMPLEMENTER

**Context package.**

- `src/django_core_micha/notifications/models.py:265-281` (`NotificationRecipient`, full model)
  and `:203-228` (`Notification`, full model) — both read in full already, exact fields/existing
  indexes as described above.
- `src/django_core_micha/notifications/views.py:164-227` (`CanonicalInboxView` +
  `CanonicalUnreadCountView`, full query logic already read) — the two consumers whose query shape
  the new indexes must match. Do not guess the shape from the model alone; these two views are the
  ground truth for which columns matter.
- `src/django_core_micha/notifications/migrations/` — latest existing migration is `0009_notification_resolved_at.py`;
  new migration is next free number (confirm at implementation time, this WO's number is
  illustrative, not to be hardcoded from memory).
- `pyproject.toml:41-44` — pytest config (`DJANGO_SETTINGS_MODULE = "tests.settings"`,
  `testpaths` already includes `src/django_core_micha/notifications/tests`). Run scoped:
  `pytest src/django_core_micha/notifications/tests/`.
- Precedent for this WO's shape and register/tier conventions: `work-orders/DCM-PERF-1.md` +
  its `WORK_ORDERS.md` row (`DCM-PERF-1`, done, `7539957`) — same class of fix (shared-core
  performance, surfaced from a jg-ferien staging capture), same Tier 3 + `reviewer`-only routing
  (no auth surface there either).

**Do-not-touch:** view logic, the todo subsystem, `iter_feed_hidden_type_keys`, any consuming app's
code (jg-ferien or otherwise) — this WO ends at the migration; consumption is a separate,
app-side pin-bump WO per app, exactly like `DCM-PERF-1` → `JG-PERF-7`.

**Target repo working directory (absolute):**
`C:/Users/Micha Bigler/Documents/webapps/django-core-micha`

## Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`,
> and this repo's own `MEMORY.md`/`WORK_ORDERS.md` ONLY for conventions. Stay in scope; do not touch
> auth/permissions/deps/CI unless the spec says so; do not update `MEMORY.md`. **Do NOT edit
> `WORK_ORDERS.md`** — the register row and review verdicts are the orchestrator's alone. **Your
> tools are for editing source and test files and for running the tests you wrote — nothing else.**
> Do NOT install dependencies, touch a lockfile, run a package manager, or tidy up stray files; if
> something in the repo state blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead
> of fixing it. Do NOT `git add`/`commit`/`push` — leave every change uncommitted in the working
> tree for the orchestrator's independent review. WRITE the tests the `Required tests` section calls
> for AND **RUN the tests you just wrote** to confirm they execute and pass — that is the ONLY test
> run you do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the
> authoritative set + does the independent review after you finish — those are the gate; your own
> run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

---

## Part C — Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**

### Execution directive

`codex exec` in the background, invoked directly via Bash, both mandatory flags. Check
`.claude/codex-status.md` (webapps root) for today's date before dispatch.

### Review routing

Tier 3, shared-core: `reviewer` only (no auth surface, no frontend) — full context, diff inline.
Reviewer should specifically verify the query-plan claim (index actually used, not just present),
same discipline `DCM-PERF-1`'s reviewer applied to the cache-invalidation claim.

### Verification

`pytest src/django_core_micha/notifications/tests/` (scoped, per this repo's testpaths). No
prototype gate (backend-only, no frontend).

### Register + commit

`WORK_ORDERS.md` row (`DCM-PERF-2`) at finalize, with review verdict and commit SHA. Commit to
`main` — this repo has no `develop` branch (confirmed via `git branch -a`, 2026-08-29). After
landing, a **separate, app-side pin-bump WO** is needed per consuming app that wants the fix (start
with jg-ferien, the app that surfaced this) — not part of this WO, exactly as `DCM-PERF-1` →
`JG-PERF-7` split.
