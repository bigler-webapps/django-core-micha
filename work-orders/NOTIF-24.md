# NOTIF-24 — the todo derivation issues per-seed writes on every read

Status: planned · Tier 2 · Target repo: `django-core-micha` (main)

---

## Part A — Envelope (Expertenchat, 2026-08-02)

### Goal

Make `derive_todos_for_user` cost a **bounded** number of queries instead of a handful per emitted seed,
and stop the bell's unread-count endpoint from paying the full materialization price.

### The problem

Reported from jg-ferien as "the dashboard notification/task query takes forever". jg owns its own share
of that (its providers rebuild their whole context once per registered type — `jg-ferien` `PERF-4`), but
two costs sit here in dcm and affect **every** consumer.

**1. Per-seed query fan-out on a read path.** `derive_todos_for_user`
(`src/django_core_micha/notifications/todo/service.py`) loops over every registered type, then over every
seed that type's provider emits, and per seed issues:

- `_get_override(scope, type_key)` — its own `TodoOverride` filter;
- `Notification.objects.get_or_create_by_dedup(...)` — a SELECT, sometimes an INSERT;
- a conditional `notification.save(update_fields=["content"])` when the stored content went stale;
- `NotificationRecipient.objects.get_or_create(...)` — a SELECT, sometimes an INSERT.

So a user with N actionable todos pays roughly 3–4 queries per todo, on a GET, several of them on the
write path.

**2. The unread badge pays for full materialization.** `CanonicalUnreadCountView`
(`src/django_core_micha/notifications/views.py`) computes `todo_count` by calling `derive_active_todos`
and summing `seen_at is None`. To produce **one integer**, it runs every provider and materializes every
notification and recipient row. The feed list view (`CanonicalNotificationListView`) does the same, which
is defensible — it actually returns those rows — but the count endpoint is called far more often, and by
a badge.

### What must NOT change — read this before proposing a design

**The writes are deliberate, not a bug.** The docstring says so explicitly: the notification and
recipient upserts exist so per-user status (`seen_at` / `dismissed_at` / `done_at`) survives across
derivations, and `get_or_create_by_dedup` is what makes that idempotent and TOCTOU-safe. A "fix" that
makes the derivation read-only would silently drop the status overlay — dismissed todos would come back
from the dead. This WO is about **batching** those writes, not removing them.

### Expected outcome

1. **Bounded queries per derivation.** Overrides resolved for the whole seed set in one query rather than
   one per seed. Existing notifications fetched by their dedup keys in one query, missing ones created in
   one bulk operation, and the same for recipients. Stale-content re-syncs batched rather than saved one
   at a time. The target shape is "a handful of queries per derivation", not "a handful per todo".
2. **Idempotency and race-safety preserved.** Whatever replaces `get_or_create` must still converge
   correctly when two requests derive the same user concurrently — the unique `dedup_key` is the backstop
   and must stay the thing that enforces it.
3. **A cheaper unread count.** The badge must not require materializing rows it never returns. Whether
   that is a counting path that skips the upserts, or a documented reason why it cannot be, is for the
   implementation to determine and report — but the current situation (a badge triggering the full write
   loop) must not simply be left as-is without a stated reason.
4. **Measured, not asserted.** Before/after query counts for a user with a realistic number of todos,
   recorded in the register row.

### Scope

`src/django_core_micha/notifications/todo/service.py`, `src/django_core_micha/notifications/views.py`
(the count path), and their tests.

### Non-goals / do-not-touch

- **Do not change what is emitted.** The set of todos, their materialized content, due dates,
  severities, ordering (`created_at` descending), and the dismissed/done filtering in
  `derive_active_todos` all stay exactly as they are.
- **Do not remove the writes** (see above).
- **Do not change the provider registry contract** — `TodoSeed`, `TodoTypeConfig`,
  `register_todo_provider`, and `candidate_users_fn` keep their current shapes. jg's `PERF-4` is being
  written against today's contract; changing it underneath would break that WO mid-flight.
- **Do not fix jg's 13×-context-rebuild here.** That is jg-local and is `PERF-4`.
- No schema change, no migration, no new model, no caching layer.

### Tier

**Tier 2.** Shared-core, on the notification write path, with concurrency semantics that are currently
guaranteed by `get_or_create` and would be re-implemented by any bulk rewrite. Codex-first, mandatory
independent review.

### Risks

- **Losing TOCTOU safety.** The single real hazard. `get_or_create_by_dedup` is safe against two
  concurrent derivations of the same user; a naive "SELECT existing, then `bulk_create` the rest" is not,
  unless it handles the conflict. This must be handled deliberately and have a test, not be assumed away.
- **Silently dropping the content re-sync.** The `if not created and notification.content != materialized`
  branch keeps existing rows current. Batching must preserve it — a stale-content row is a wrong due date
  or severity shown to a user, which looks like a data bug, not a performance regression.
- **Diverging count and list.** If the count endpoint gets a cheaper path, it must still agree with what
  the feed list would show; a badge that disagrees with the list it links to is worse than a slow badge.
  This needs an explicit equivalence test.
- **Consumer breadth.** Every app registering todo providers is affected. Today only jg does, which makes
  this the right moment to change it — say so rather than assuming it stays that way.

### Tests to WRITE (scoped — run only these)

- **query-count regression**: deriving a user with many todos across several types stays under a pinned
  ceiling, and the ceiling does not scale with the number of todos the way it does today;
- **output parity**: the derived recipient set (and each notification's materialized content) is
  identical to the pre-change behaviour for a fixture spanning several types, including one with an
  override and one with a lead-days override;
- **status overlay survives**: a dismissed and a done todo stay dismissed/done across two consecutive
  derivations, and stay omitted from `derive_active_todos`;
- **stale content is re-synced**: an existing notification whose stored content diverges from the freshly
  materialized payload is updated;
- **concurrency**: two derivations of the same user converge without an integrity error and without
  duplicate rows (the TOCTOU assertion — this is the one that must not be skipped);
- **count/list agreement**: the unread count equals the number of unseen rows the feed list returns for
  the same user, whatever path the count takes.

### Preconditions and ordering

None inbound. Independent of jg's `PERF-4` — either may land first. If both land, jg should re-measure
afterwards, since the two multiply rather than add.

### Release

One version bump + publish at WO end, per this repo's publish-from-main flow. jg pins the new version
only if it needs to; nothing in `PERF-4` depends on it.

### Approval Gate #1

Granted by the operator 2026-08-02 ("Ja, WO für dcm und jg").

### Mini-handover (pastable)

Orchestrator: implement `work-orders/NOTIF-24.md` in `django-core-micha` (main). `git pull` first, read the
WO, then follow `orchestrate-codex` (write Part B yourself, Codex-first, own independent review, publish at
WO end). **Measure before changing code and report the numbers.**

---

## Part B — Implementation map

Owned by the Orchestrator session, deliberately not written here.
