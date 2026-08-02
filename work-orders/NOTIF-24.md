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

### Execution directive (read this first if you are the implementer)

> **If you are the implementer reading this work order as your own specification: this section is
> NOT addressed to you.** It tells the Orchestrator how to invoke you. **You ARE that invocation —
> do NOT shell out to `codex exec`.**
>
> Implement through `codex exec` in the background — invoked directly via Bash (never the
> `debugger`/`*_coder` Agent wrappers) with BOTH flags `--skip-git-repo-check` and
> `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file.
> (Fallback to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.)

### Measurement — done 2026-08-02 (mandatory first step, per Envelope)

Measured directly with `CaptureQueriesContext`, standalone (`tests.settings`, sqlite, no Docker
needed for this repo) against `derive_active_todos`: a synthetic user with 13 registered todo types
(matching jg's `REGISTERED_TODO_TYPES` count), each emitting one seed scoped to its own object —
13 actionable todos, a realistic per-user count.

- **Cold run** (first derivation, creates all rows): **39 total queries**, all resolving as
  `SELECT`/`INSERT` pairs per seed (≈3 queries/seed: override lookup, `get_or_create_by_dedup`,
  `NotificationRecipient.get_or_create`) — matches the envelope's "3–4 queries per todo" estimate.
- **Warm run** (second call, immediately after, nothing changed): **also 39 total queries** — not
  fewer. This is the sharper finding: even when there is nothing to write, `derive_active_todos`
  pays the *same* per-seed SELECT cost every single call (override filter + dedup SELECT + recipient
  SELECT, all repeated), because nothing short-circuits when content is already fresh and rows
  already exist. That is exactly what "bounded queries per derivation" (Expected outcome #1) needs to
  fix, and directly explains why the badge (`CanonicalUnreadCountView`) is expensive even though
  most calls are steady-state, not first-materialization.

**Verdict: envelope confirmed, no disagreement to report.** ~3 queries/seed cold matches the stated
estimate; the unread-count badge genuinely re-pays that per-seed cost on every GET, confirming
Expected outcome #3's premise.

### Context package

**1. Per-seed fan-out — `src/django_core_micha/notifications/todo/service.py`**

```python
def derive_todos_for_user(user, now=None) -> list[NotificationRecipient]:   # line 39
    resolved_now = now or timezone.now()
    emitted: dict[int, NotificationRecipient] = {}
    for type_key in iter_registered_todo_types():
        provider_fn = get_todo_provider(type_key)
        config = get_todo_config(type_key)
        for seed in provider_fn(user, resolved_now):
            scope = seed.scope if seed.scope is not None else seed.notifiable
            override = _get_override(scope, seed.type_key)                       # Q: per seed
            if override is not None and not override.enabled:
                continue
            effective_engine_config = _engine_config(_apply_lead_override(config, override))
            materialized = materialize_todo(seed.content, effective_engine_config, resolved_now, ...)
            if materialized is None:
                continue
            notification, created = Notification.objects.get_or_create_by_dedup(   # Q: per seed
                notification_type=seed.type_key, category="todo",
                notifiable=seed.notifiable, content=materialized, urgency="normal",
            )
            if not created and notification.content != materialized:
                notification.content = materialized
                notification.save(update_fields=["content"])                        # Q: conditional
            recipient, _ = NotificationRecipient.objects.get_or_create(              # Q: per seed
                notification=notification, user=seed.recipient,
            )
            recipient.notification = notification
            notification._todo_due_at = resolve_due_date(...)
            emitted[notification.pk] = recipient
    return sorted(emitted.values(), key=lambda r: r.notification.created_at, reverse=True)
```

`_get_override` (line 13):
```python
def _get_override(scope, type_key: str):
    if scope is None:
        return None
    return TodoOverride.objects.filter(
        content_type=ContentType.objects.get_for_model(scope), object_id=str(scope.pk), type_key=type_key,
    ).first()
```

**Dedup/idempotency backstop (do not weaken):** `Notification.get_or_create_by_dedup`
(`models.py:131`) computes `dedup_key = build_dedup_key(notification_type, notifiable)` and calls
Django's own `get_or_create(dedup_key=..., defaults=...)`; TOCTOU-safety comes from
`models.UniqueConstraint(fields=["dedup_key"], name="uniq_notification_dedup_key")` (`models.py:191`) —
two concurrent `get_or_create` calls racing on the same `dedup_key` are resolved at the DB constraint
level (one wins, the other's `get_or_create` retries the SELECT after the `IntegrityError`, which is
Django's standard `get_or_create` behaviour). `NotificationRecipient` has its own
`unique_together = ("notification", "user")` (`models.py:221`) as the analogous backstop for the
recipient upsert. **Any batched replacement must preserve both constraints as the enforcement
mechanism** — e.g. `bulk_create(..., update_conflicts=True, unique_fields=["dedup_key"], update_fields=[...])`
(Django 6, available per `pyproject.toml`) or an explicit "SELECT existing by dedup_key IN (...),
diff, bulk_create the rest, handle `IntegrityError` on the race" pattern — not a plain
"SELECT then unconditionally bulk_create" which reintroduces the TOCTOU hole `get_or_create_by_dedup`
exists to close. Existing bulk-write precedent in this codebase:
`src/django_core_micha/messaging/services.py:257` — `bulk_create([...], ignore_conflicts=True)`
(similar shape, simpler conflict handling since it doesn't need to update existing rows).

**Provider registry contract — frozen, do not change these shapes**
(`src/django_core_micha/notifications/todo/registry.py`):
```python
@dataclass(frozen=True)
class TodoSeed:
    type_key: str
    recipient: Any
    content: dict
    notifiable: Any | None = None
    scope: Any | None = None
    due_base_resolver: Callable[[str], Any] | None = None
    has_due_time: bool = False

@dataclass(frozen=True)
class TodoTypeConfig:
    type_key: str
    due: str | None = None
    remind_before: str | None = None
    severity: str | None = None
    persist_until_done: bool = False
    always_visible: bool = False
    lead_adjustable: bool = False

def register_todo_provider(type_key, provider_fn, *, config, candidate_users_fn=None) -> None: ...
```
jg's `PERF-4` is being implemented against this exact contract concurrently — do not touch this file.

**2. The unread-count badge — `src/django_core_micha/notifications/views.py`**

```python
class CanonicalUnreadCountView(views.APIView):          # line 198
    def get(self, request):
        event_count = NotificationRecipient.objects.filter(
            user=request.user, seen_at__isnull=True, dismissed_at__isnull=True,
        ).exclude(notification__category="todo").exclude(
            notification__notification_type__in=iter_feed_hidden_type_keys()
        ).count()
        todo_count = sum(recipient.seen_at is None for recipient in derive_active_todos(request.user))  # <- full cost
        return Response({"count": event_count + todo_count})
```

Sibling `CanonicalInboxView.get_queryset` (line 150) is the feed list — it legitimately needs the
full materialized `live_todos` list since it returns those rows; it already short-circuits when
`not iter_registered_todo_types()` (line 176), which is the existing precedent for "skip the
provider loop when it can't possibly matter" — same idea can inform the count path's cheaper case,
though here providers usually *are* registered (jg registers 13), so the real fix is making the
per-call cost bounded (part 1) rather than skipping it. **Count/list agreement is a hard requirement**
(Envelope Risks) — whatever path `todo_count` takes must count exactly the rows `CanonicalInboxView`
with `status=unseen` would return for `category="todo"`.

**3. Tests to extend**

- `src/django_core_micha/notifications/tests/test_todo_service.py` — existing fixtures
  (`register_provider`, `make_user_and_widget`, the `clear_registries` autouse fixture) are the base
  to build the query-count regression, concurrency, and stale-content-resync tests on. No
  `CaptureQueriesContext` usage exists yet in this file — `src/…/tests/test_auth_perf.py:146-156` is
  this repo's existing pattern to copy (`with CaptureQueriesContext(connection) as ctx: ...`).
- Concurrency/TOCTOU test: two derivations of the same user racing on the same seed set must not
  raise `IntegrityError` and must not create duplicate `Notification`/`NotificationRecipient` rows —
  simulate via two sequential calls sharing a `dedup_key` that already exists is not sufficient; must
  genuinely exercise the batched-write path's conflict handling (e.g. pre-creating one row out of
  band between "select existing" and "bulk_create the rest" if the implementation takes that shape,
  or an actual threaded/transaction-interleaved test if using Django's `TransactionTestCase`).
- `src/django_core_micha/notifications/tests/test_canonical_notification_api.py` — extend for the
  count/list agreement test (Envelope-mandated).

### Invariants / do-not-touch (repeat of Envelope, do not weaken)

- Do not remove the writes — the docstring in `service.py:39-50` explains why they exist (status
  overlay survival across derivations). Batch them; do not make the derivation read-only.
- Do not change `TodoSeed` / `TodoTypeConfig` / `register_todo_provider` / `candidate_users_fn` shapes.
- No schema change, no migration, no new model, no caching layer (this is about query count, not
  about caching across requests).
- Preserve `derive_active_todos`'s dismissed/done filtering and `created_at` descending order exactly.
- Do not touch jg's `todo_channel.py` — that is `PERF-4`, a separate repo/WO.

### Release (per this repo's flow — publish-from-main, no staging)

One version bump (`pyproject.toml`) + `CHANGELOG.md` entry + publish at WO end. jg pins the new
version only if/when it needs to; nothing in `PERF-4` depends on this release landing first.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha`

### Preamble (append verbatim)

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. Do NOT `git add`/`commit`/`push` — leave every
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
