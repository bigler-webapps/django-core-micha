# DCM-NOTIF-10 — A problem that recurs after resolution never notifies again

Repo: `django-core-micha` · Branch: `main` · Tier: **3** · Status: planned

Found in `cockpit` (`CKP-MON-15`) while probing a new notification type, and confirmed against
`status.monitor_down` — the oldest and most-used status alert in the estate. The defect is in shared
core, so `CKP-MON-15` is the right home for the finding and this is the right home for the fix.

---

## Part A — Envelope (authoritative WHAT/WHY)

### Goal

Emitting a notification for a problem that was previously resolved delivers again. Emitting one for
a problem that is still open continues to deduplicate, exactly as today.

### Why — measured and explained, both

**Measured** (probe against a local stack, cockpit session, 2026-08-26):

```
emit    -> open=True   done_at=[None]
resolve -> open=False  done_at=[<ts>]
emit    -> open=False  done_at=[<ts>]   push_called=0
```

The second emit produces no delivery, no new recipient row, no push. Reproduced identically against
`status.monitor_down`.

**Explained** (`notifications/models.py`): `build_dedup_key` is
`sha256(f"{notification_type}:{app_label}.{model}:{pk}")` — permanent and stateless. It carries no
episode, no timestamp, no notion of "this one was already resolved". `get_or_create_by_dedup` then
*gets* the existing row, and the emit path adds nothing to it. Nothing expires it either: neither
`emit_status_event` nor `emit_app_alert` passes `expires_at`, and dcm's only expiry handling is for
push *subscriptions*.

**So the practical consequence is literal: a monitor that goes down, recovers, and goes down again
alerts exactly once — ever.** Every monitor in every dcm consumer that has already alerted once and
recovered is, today, silent for all future occurrences. This predates every other work order in the
`CKP-MON-*` series and outweighs all of them: correlation, suppression and gating are worth nothing
if the delivery never happens.

### The decision, already taken

**Operator decision 2026-08-26: a resolved notification is never reused.** An emit that follows a
resolution is a new occurrence, whatever the type — the alternative (an explicit per-type "episodic"
flag) was considered and rejected as more machinery for the same result.

The distinction being fixed is a single mechanism carrying two meanings: a **standing fact** ("this
document needs review") and an **episode** ("the host is down"). Permanent dedup is right for the
first and wrong for the second, and re-notifying a resolved standing fact is defensible in its own
right — it means the fact came back.

### The crux: the unique constraint

`Notification.dedup_key` carries `UniqueConstraint(fields=["dedup_key"], name="uniq_notification_dedup_key")`.
There can be at most one row per (type, target), forever. **So "do not reuse a resolved
notification" cannot be implemented without addressing that constraint**, and the choice has
consequences beyond dcm:

- **Relax uniqueness to "at most one OPEN notification per key"** (a partial constraint). This
  expresses exactly what the system already means — `has_open_problem` is precisely that question —
  and keeps keys stable and readable.
- **Or give the key an episode component**, so each occurrence is its own key. Keys stop being
  derivable from (type, target) alone, which every current caller assumes.

The invariant that must hold either way: **at most one open notification per (type, target) at any
time.** The implementation may choose the shape; it may not weaken that.

### Scope

1. **An emit after resolution creates and delivers a new notification.**
2. **An emit while the previous one is still open continues to deduplicate**, unchanged. This is not
   a secondary concern — it is what stops a five-minute poller from sending an alert every five
   minutes, and breaking it would replace silent under-alerting with a flood.
3. **Address the unique constraint** per the crux above, with a migration.
4. **Migrate every consumer call site in the same work.** `dedup_key` is referenced outside dcm:
   **cockpit (7), jg-ferien (8), spesix (1)** — counted 2026-08-26, verify before starting. The
   pattern `Notification.objects.get(dedup_key=...)` becomes wrong the moment a second episode can
   exist: it raises `MultipleObjectsReturned`. Leaving it would convert silent under-alerting into a
   crash in three applications.
5. **Ship it end-to-end.** A shared-core fix is not done when it is published — the consuming apps'
   pins must be bumped and deployed, or nothing changes for anyone.

### Non-goals / do not touch

- **No per-type special case, and no "episodic" flag.** That was the rejected alternative; adding it
  as a fallback would reintroduce the two-meanings problem the fix removes.
- **No consumer-side workaround.** A cockpit-local dedup identity was proposed during `CKP-MON-8` and
  rejected: it would decouple one app from every other dcm consumer, and the next app with the same
  problem would have to solve it again.
- No change to routing, channel preferences, digests, the todo channel, or delivery mechanics beyond
  what the constraint change forces.
- No change to what "resolved" means: every recipient carrying `done_at`. This WO changes what
  happens *next*, not the definition.

### Risks

1. **`MultipleObjectsReturned` in three consumer apps** — scope item 4. This is the way the fix does
   harm, and it does it loudly and immediately. Every call site must be surveyed, not sampled.
2. **A migration on a table with production data**, in every consuming app. Existing rows all share
   the current key shape; the migration must leave already-resolved history intact and must not
   collapse or duplicate open problems.
3. **The reverse failure: new noise.** Types where permanent dedup was quietly load-bearing will now
   re-notify. Survey which notification types are actually in use across consumers and check each
   against "would a repeat after resolution be wanted here" — the operator's decision says yes in
   general, but a type that resolves and recurs many times a day would be a finding worth reporting
   before shipping, not after.
4. **Fixing this makes the estate louder in the short term**, because monitors that have been silent
   will start alerting again. That is the point, and it should be expected rather than read as a new
   outage.

### Required tests to WRITE (narrow — this change's own)

- **The measured scenario**: emit → resolve → emit produces a **second delivery**, with a new
  recipient row.
- **The pin on the other side**: emit → emit while still open produces **one** delivery. This
  no-regression assertion matters as much as the first — the two together are what keep the fix from
  swinging into a flood.
- At most one open notification per (type, target) — the invariant, asserted directly.
- `has_open_problem` and a resolution helper still answer correctly once a second episode exists.
- Consumer-facing: a `get(dedup_key=...)`-shaped lookup no longer raises with two episodes present.
- **Mutation-check the first two**: the first must fail against today's permanent reuse, the second
  against an implementation that deduplicates nothing.

Not required: a full suite run here. The consuming apps' own suites run on their pin bumps.

---

## Part B — Implementation map

**Scope of THIS dispatch: `django-core-micha` only** (`src/django_core_micha/notifications/`).
Consumer call-site migration (cockpit/jg-ferien/spesix) is a separate, later step the Orchestrator
drives after this lands — see Part C.

### Design (already decided by the Orchestrator — implement exactly this shape)

Relax the constraint variant (crux, Part A): add a nullable `resolved_at` on `Notification` and make
the uniqueness **partial** — at most one row per `dedup_key` where `resolved_at IS NULL`. A resolved
row is never reused; `get_or_create_by_dedup` and the dedup lookups only ever see the OPEN row.
Nothing sets `resolved_at` today, so every existing consumer flow that never calls the new resolve
helper (notably dcm's own `todo/service.py`) keeps behaving exactly as before — this is deliberate
and requires **no change to `todo/service.py`**: do not touch it.

### Context package

**1. `src/django_core_micha/notifications/models.py`**
- Add `from django.utils import timezone` to the imports (top of file, alongside the existing
  `from django.db.models import Q`).
- On `Notification` (around `models.py:214-216`, between `expires_at` and `created_at`), add:
  ```python
  resolved_at = models.DateTimeField(null=True, blank=True)
  ```
- In `Notification.Meta.constraints` (`models.py:225-227`), replace
  ```python
  models.UniqueConstraint(fields=["dedup_key"], name="uniq_notification_dedup_key"),
  ```
  with
  ```python
  models.UniqueConstraint(
      fields=["dedup_key"],
      condition=Q(resolved_at__isnull=True),
      name="uniq_notification_dedup_key_open",
  ),
  ```
- Add an instance method on `Notification`:
  ```python
  def mark_resolved(self, *, when=None):
      """Close this notification's open episode; a later emit for the same
      (type, target) starts a new one instead of reusing this row."""
      if self.resolved_at is None:
          self.resolved_at = when or timezone.now()
          self.save(update_fields=["resolved_at"])
  ```
- On `NotificationManager` (`models.py:163-189`):
  - In `get_or_create_by_dedup`, change the final line from
    `return self.get_or_create(dedup_key=dedup_key, defaults=defaults)` to
    `return self.get_or_create(dedup_key=dedup_key, resolved_at__isnull=True, defaults=defaults)`.
    (Django's `get_or_create` strips `__`-lookup keys from the `create()` call automatically, so
    this does not try to pass `resolved_at__isnull` to `Notification(...)` — the created row's
    `resolved_at` is simply left at its field default, `None`.)
  - Add a new manager method:
    ```python
    def get_open(self, notification_type, notifiable):
        """Return the open (unresolved) Notification for this type+target, or None."""
        dedup_key = self.model.build_dedup_key(notification_type, notifiable)
        return self.filter(dedup_key=dedup_key, resolved_at__isnull=True).first()
    ```

**2. `src/django_core_micha/notifications/api.py`**
- `_get_notification_with_retry` (`api.py:30-44`): change the `except IntegrityError` fallback from
  `Notification.objects.get(dedup_key=dedup_key)` to
  `Notification.objects.get(dedup_key=dedup_key, resolved_at__isnull=True)` — the concurrent insert
  that raced us created (or matched) the open row, so this must look at the same open row, not
  whichever row the dedup_key happens to return.
- Add two new public functions near the bottom of the file (after `notify_subscribers`):
  ```python
  def resolve(*, type, notifiable) -> Notification | None:
      """Close the open episode for `type`+`notifiable`: mark every not-yet-done
      recipient done and close the notification, so the next emit for the same
      (type, target) starts a fresh episode instead of silently reusing this one.
      Returns None if there is no open notification to resolve.
      """
      notification = Notification.objects.get_open(type, notifiable)
      if notification is None:
          return None
      NotificationRecipient.objects.filter(
          notification=notification, done_at__isnull=True,
      ).update(done_at=timezone.now())
      notification.mark_resolved()
      return notification


  def has_open(*, type, notifiable) -> bool:
      """Is there an open (unresolved) Notification for `type`+`notifiable`?"""
      return Notification.objects.get_open(type, notifiable) is not None
  ```
  `timezone` is already imported in this file (`api.py:5`). These two functions are the canonical
  primitives that cockpit's `notify.services.has_open_problem`/`resolve_status_problem` will be
  migrated onto in a **later, separate** dispatch (Part C) — do not touch cockpit/jg-ferien/spesix
  from this WO.

**3. New migration `src/django_core_micha/notifications/migrations/0009_notification_resolved_at.py`**
(dependency: `("notifications", "0008_notificationcategorysubscription")` — confirm this is still
the latest migration in the directory before writing the dependency; if a newer one has landed,
depend on that instead). Shape:
  ```python
  from django.db import migrations, models
  from django.db.models import Count, Max


  def backfill_resolved_at(apps, schema_editor):
      Notification = apps.get_model("notifications", "Notification")
      open_ids = set(
          Notification.objects.filter(recipients__done_at__isnull=True)
          .values_list("pk", flat=True)
      )
      closed = (
          Notification.objects.exclude(pk__in=open_ids)
          .annotate(n_recipients=Count("recipients"), last_done=Max("recipients__done_at"))
          .filter(n_recipients__gt=0)
      )
      for notification in closed.iterator():
          notification.resolved_at = notification.last_done or notification.created_at
          notification.save(update_fields=["resolved_at"])


  def noop(apps, schema_editor):
      pass


  class Migration(migrations.Migration):
      dependencies = [
          ("notifications", "0008_notificationcategorysubscription"),
      ]

      operations = [
          migrations.AddField(
              model_name="notification",
              name="resolved_at",
              field=models.DateTimeField(blank=True, null=True),
          ),
          migrations.RunPython(backfill_resolved_at, noop),
          migrations.RemoveConstraint(
              model_name="notification",
              name="uniq_notification_dedup_key",
          ),
          migrations.AddConstraint(
              model_name="notification",
              constraint=models.UniqueConstraint(
                  condition=models.Q(("resolved_at__isnull", True)),
                  fields=("dedup_key",),
                  name="uniq_notification_dedup_key_open",
              ),
          ),
      ]
  ```
  **Why the backfill excludes zero-recipient rows:** a notification with no recipient rows yet is
  "nobody to notify", not "problem resolved" — it must stay open (`resolved_at` stays `None`), hence
  `filter(n_recipients__gt=0)`. A row with at least one recipient and none of them open (`done_at`
  not null on every recipient) is exactly "every recipient carrying `done_at`" — Part A's own
  definition of resolved — so it gets backfilled closed, using the latest `done_at` as the
  best-available historical resolution time (fallback to `created_at` only if that is somehow null,
  which should not happen once `n_recipients__gt=0`).

### Required tests to WRITE (in `src/django_core_micha/notifications/tests/`, new cases — add to
`test_notification_models.py` and/or `test_notification_api.py`, whichever fits the existing file's
style; do not create a new test file)

1. **Measured scenario, end to end**: `notify()` → `api.resolve()` → `notify()` (same type, same
   notifiable, same content) produces a **second, distinct** `Notification` row; the first has
   `resolved_at` set, the second does not; the second has its own fresh `NotificationRecipient` (and,
   with `dispatch` monkeypatched the way `test_notification_api.py`'s existing tests do, a fresh
   delivery). This is the test that must **fail** against today's code (permanent reuse) — confirm
   that by eye against the diff before calling it done, per Part A's mutation-check requirement.
2. **No-regression pin**: `notify()` → `notify()` with **no** `resolve()` in between still
   deduplicates to **one** row (already covered by
   `test_notify_creates_canonical_rows_dispatches_and_deduplicates`, but add an explicit assertion
   this stays true immediately after the new `resolved_at` field exists — this is the test that must
   fail against an implementation that broke dedup entirely, e.g. one that dropped the
   `resolved_at__isnull=True` filter from `get_or_create_by_dedup`).
3. **Invariant, asserted directly**: creating a second `Notification.objects.create(...)` with the
   same `dedup_key` while the first is still unresolved raises `IntegrityError`; after calling
   `.mark_resolved()` on the first, a second `create()` with the same `dedup_key` succeeds, and
   `Notification.objects.filter(dedup_key=...).count() == 2`.
4. **Helpers after a second episode exists**: build a first open notification via
   `get_or_create_by_dedup`, call `api.resolve(...)`, build a second via `get_or_create_by_dedup`
   again (assert `created is True` and a different pk) — then assert `Notification.objects.get_open(...)`
   and `api.has_open(...)` both correctly report the SECOND row / `True`, and after resolving the
   second, `api.has_open(...)` is `False` while both rows still exist
   (`Notification.objects.filter(dedup_key=...).count() == 2`).
5. **Consumer-shaped lookup survives two episodes**: with two episodes present (one resolved, one
   open) for the same dedup_key, `Notification.objects.get(dedup_key=..., resolved_at__isnull=True)`
   returns the open one without raising `MultipleObjectsReturned` — this is the shape every consumer
   call site will be migrated to.

Not required here: touching `todo/service.py` or its tests (unaffected, see Design above); the
consumer repos' own tests (their own suites, on their own pin bumps, later).

## Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha`

## Preamble — REQUIRED, part of this file

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`,
> and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/
> schema/CI beyond the one migration this spec calls for; do not update `MEMORY.md`. **Do NOT edit
> `WORK_ORDERS.md` — the register row and the review verdicts are the orchestrator's alone.** **Your
> tools are for editing source and test files and for running the tests you wrote — nothing else.**
> Do NOT install dependencies, touch a lockfile, run a package manager, or tidy up stray files; if
> something in the repo state blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of
> fixing it. Do NOT `git add`/`commit`/`push` — leave every change uncommitted in the working tree for
> the orchestrator's independent review. WRITE the tests the `Required tests` section calls for AND
> **RUN the tests you just wrote** to confirm they execute and pass — that is the ONLY test run you
> do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the authoritative
> set + does the independent review after you finish — those are the gate; your own run does not
> count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

---

## Part C — Orchestrator only

> **STOP — everything below this line addresses the Orchestrator, not the implementer.**
> If you are implementing this work order, your instructions end above.

### This dispatch's scope

`django-core-micha` only, per Part B. The consumer-repo work (below) is driven by the Orchestrator as
separate, later dispatches — not part of this `codex exec` invocation.

### Execution directive

Implement through `codex exec` in the background, invoked directly via Bash (never the
`debugger`/`*_coder` Agent wrappers), with BOTH `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`. Fallback to direct Claude implementation only on Codex
quota/rate-limit/non-zero exit — check `.claude/codex-status.md` first per `AGENTS.md`.

### Review routing

Tier 3, shared core: `reviewer` + `sec_reviewer` concurrent, full context, plus the Orchestrator's own
targeted pass. Named review questions: (a) does `get_or_create_by_dedup`'s `resolved_at__isnull=True`
filter actually stop a resolved row from being reused — read the manager method, not just the tests;
(b) does the partial `UniqueConstraint` correctly express "at most one open row per dedup_key" for
Postgres (condition on the indexed table's own column, no cross-table reference — verify it compiles,
i.e. the migration applies cleanly); (c) is the backfill's zero-recipient exclusion correct, i.e. does
it genuinely never mark an unresolved problem as resolved; (d) confirm `todo/service.py` was not
touched.

### Verification

`pytest src/django_core_micha/notifications/` (the affected-areas set — this migration and model
touch the whole app) run from the repo root, authoritative run by the Orchestrator, not Codex's own.
No prototype artifact in scope (backend-only). Also apply the migration against a real Postgres (not
just `--check`) to confirm the partial constraint DDL is valid before calling this done.

### Consumer call-site migration (separate dispatches, after this lands and is reviewed+tested+committed)

Three consumers reference `dedup_key`; only cockpit needs a code change — the other two are already
safe under the new schema (verified 2026-08-27 by direct read, not by the original 2026-08-26 count
alone):

- **cockpit** (`backend/notify/services.py:41-53` `has_open_problem`, `:94-107`
  `resolve_status_problem`): both use `Notification.objects.get(dedup_key=dedup_key)` — this becomes
  `MultipleObjectsReturned` the moment a second episode exists. Migrate them onto the new dcm
  primitives: `has_open_problem` → thin wrapper around `django_core_micha.notifications.api.has_open`
  (or inline the `get_open(...) is not None` check); `resolve_status_problem` → wrapper around
  `django_core_micha.notifications.api.resolve`. `backend/status/celery_tasks.py:1920` already uses
  `.filter(...)` (safe, no change needed). Needs cockpit's own Tier-appropriate WO + pin bump + its
  own affected-tests run (`backend/tests/test_alert1_correlation.py`,
  `backend/tests/test_status_notifications.py`, `backend/tests/test_status_notification_gate.py`) +
  deploy.
- **jg-ferien**: `events/todo_channel.py:198` uses `.filter(...).first()` (safe);
  `events/todo_canonical.py:231` uses `get_or_create` on a synthetic, never-resolved dedup key (safe,
  stays a single row forever); `:241` uses `get_or_create_by_dedup` (fixed centrally by this WO once
  the pin bumps). **No code change needed** — pin bump + its own affected tests only.
- **spesix**: `backend/workflow/notifications.py:84-86` `clear_approval_notification` uses
  `Notification.objects.filter(dedup_key=...).delete()` (safe — `.delete()` doesn't raise on multiple
  rows). **No code change needed** — pin bump + its own affected tests only.

Sequence per `AGENTS.md`'s end-to-end rule: this WO's dcm change lands and releases (version bump +
publish) first; then cockpit's call-site fix + all three consumers' pin bumps, in any order; then each
consumer's own affected-tests run; then deploy (push to `develop`). **This register row reaches `done`
only when all three consumers are deployed on the new pin** — track per-app status in the Notiz as
each completes.

### Register + commit

`WORK_ORDERS.md` row for `DCM-NOTIF-10`: `in-progress` after this dcm dispatch lands (not `done` —
consumers still pending), with the review verdict (`reviewer + sec_reviewer: ...`) and this dcm
commit SHA. Move to `done` only once all three consumer pins are bumped and deployed, per above.
