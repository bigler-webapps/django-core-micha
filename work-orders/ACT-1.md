# WORK ORDER ACT-1 (django-core-micha) — a shared user-activity domain, scoped to the host's structural object

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). New `ACT-*` prefix in dcm for the activity domain. Operator,
2026-08-04: every app needs activity tracking; jg-ferien is simply the only one that has had time to
build it.

## WHY NOW

jg-ferien has a working implementation. **It is already 90 % generic** — exactly one field is
app-specific:

```python
class EventActivityBucket(models.Model):          # jg: events/models.py:528-545
    event = models.ForeignKey(Event, …)           # <- the ONLY app-specific part
    user = models.ForeignKey(AUTH_USER_MODEL, …)
    bucket_start = models.DateTimeField()
    active_seconds = models.PositiveIntegerField(default=0)
    last_ping_at = models.DateTimeField(null=True, blank=True)
    unique_together = (("event", "user", "bucket_start"),)
```

Swap that FK for dcm's proven generic-scope pattern and the model is done.

**Moving it now is cheaper than later**: one consumer with one dataset, versus four consumers with live
data in each. That argument only holds because the operator has confirmed real demand — an earlier
draft of this analysis argued the opposite from "no other app has it today", which measured existing
implementations rather than need. Do not re-derive that mistake.

## TIER
Tier 2 — a new shared domain with a schema, a write endpoint and a query endpoint, consumed by every
app. Independent `reviewer` mandatory; `sec_reviewer` for the endpoints (they record and expose
per-user presence).

## THE DESIGN — grounded in three real cases, not one

Operator, 2026-08-04: activity scopes to **the host's structural container** —

| App | Scope object |
|---|---|
| jg-ferien | `Event` |
| spesix | School |
| survey_app | `ProjectSite` |

**Follow `MessagingScope`'s pattern exactly** (`messaging/models.py:54-63`): `content_type` FK +
`object_id` + `GenericForeignKey`, with the uniqueness constraint including the app key. It is proven
in this codebase and keeps dcm ignorant of what the object *is*.

### Who decides the scope level — the app, entirely

dcm provides the *mechanism* (any model can be a scope). **The consuming app decides which of its models
that is**, and dcm never learns the answer. jg pings with an `Event`, spesix with a School, survey_app
with a `ProjectSite`.

An app may use **several** scope levels if it wants — different content types produce separate rows and
separate charts, with no extra machinery.

**But there is no cross-scope roll-up, and there must not be.** Activity recorded against Event 4 does
**not** automatically count toward the organisation that owns Event 4: dcm cannot know that hierarchy
without learning the consumer's domain, which is the one thing this design forbids.

So an app that wants organisation-level activity has exactly two honest options, and must pick one:

1. **Ping at that level too** — the client records against the organisation as its own scope. Simple,
   and the two levels are then independent measurements.
2. **Query per child scope and aggregate app-side** — only the app knows which events belong to which
   organisation, so only the app can sum them.

**Do not add a parent/child field, a hierarchy table, or a `roll_up_to` parameter to dcm.** If a
consumer needs hierarchy, that is the consumer's knowledge and belongs on its side.

**dcm must never learn a consumer's domain.** No `event_id`, no FK to a consumer model. If the
implementation starts needing to know what the scope object means, the shape is wrong — stop and report.

**Metrics are aggregations, not stored fields.** The two series jg charts today both derive from these
same rows: distinct users is `COUNT(DISTINCT user)` per bucket, presence time is `SUM(active_seconds)`.
So there is **no metric-extensibility problem to solve** — store raw rows, aggregate on read. Do not
build a configurable metric registry.

## SCOPE

**A. The model.** As above: generic scope + user + `bucket_start` + `active_seconds` + `last_ping_at`,
uniqueness on (app, scope, user, bucket_start). Index for the read pattern (scope + bucket range), which
is what every chart query does.

**B. Storage granularity: one hour, fixed platform-wide (operator, 2026-08-04).**

An earlier draft of this WO made bucket width per-app configurable. **The operator dropped that** — the
user's range choice determines the displayed resolution, and an app-level knob adds a dimension nobody
needs. There is one storage width for everyone.

It must be **one hour**, because the finest view the operator wants is a single day on an hourly grid
(scope D). A coarser store cannot serve it: jg's current 4-hour buckets cannot be decomposed into hours
after the fact.

**Consequence to plan for, not discover:** hourly storage is **four times** the rows jg writes today —
24 per user per day per scope instead of 6. That makes retention (scope E) and the read index
(scope A) load-bearing rather than tidy-up. State the expected row volume for a realistic scope in the
completion note.

**C. The ping endpoint.** Port jg's `activity-ping` behaviour: upsert the bucket for (scope, user,
current bucket start), accumulate `active_seconds`, stamp `last_ping_at`. Take jg's implementation as
the reference — it works — rather than reinventing the accumulation rule.

**D. The query endpoint — and it MUST aggregate server-side.**

Takes a scope, a `from`/`to` range, and a **granularity**. Returns buckets already rolled up to that
granularity.

This is not optional convenience. The operator wants the user to pick **1 week / 1 month / 1 year**. At
a 4-hour storage bucket, one year is **~2190 buckets per user** — returning raw rows would ship an
unusable payload and force the client to aggregate what SQL should. **Roll up in the database.**

**Supported granularities (operator, 2026-08-04) — the resolution follows the range, and is not an
independent control:**

| Range | Resolution | Points |
|---|---|---|
| 1 day | 1 hour | 24 |
| 1 week | 4 hours | ~42 |
| 1 month | 1 day | ~30 |
| 1 year | 1 month | 12 |

Every preset returns between a dozen and fifty points, which is what keeps the chart readable at any
range. A request for a granularity **finer than the one-hour store** must fail clearly rather than
silently return something coarser.

### Where the scope comes from — the host's existing context, never a picker

The consuming app already knows which scope the user is in: jg's `selectedEventId` (`StructureContext`),
spesix's `activeSchoolId` (`SchoolContext`). **The host reads its own context and passes
`content_type` + `object_id`.** There is no scope selector to build, in dcm or in ucm.

**Activity therefore belongs where the context lives, not on an account page.** `/account` answers "who
am I"; activity answers "what is happening in this scope" — the same split that keeps the context picker
out of SHELL-1's user menu. jg already places it correctly (`EventInfoHub/ActivitySection.jsx`, rendered
for the selected event); spesix's belongs on the school surface, survey_app's on the site surface.

A long scope dropdown would only be needed for a view standing *outside* any context. **Do not build
one.** A cross-scope overview ("activity across all my events") is a different feature, it needs
app-side aggregation because dcm has no hierarchy, and it is out of scope here.

### Anchoring (operator, 2026-08-04 — keep it simple or drop it)

A range relative to *now* is useless for a finished scope: a camp that ran in July shows an empty
"last 7 days" when opened in August.

**Resolution order — one expression, three fallbacks:**

```
anchor = supplied_anchor  or  MAX(bucket_start) for the scope  or  now
```

1. **The app may supply an anchor date.** jg would pass the event's end date. This is the *preferred*
   input where the app has a meaningful date, because it is stable.
2. **Otherwise dcm derives it** from the scope's most recent bucket with data.
3. **Otherwise now**, for a scope that has never recorded anything.

**Why the app-supplied anchor matters — a flaw in the derived one.** An earlier draft of this WO used
only the derived anchor. That drifts: the moment anyone opens the July event's page today, their own
ping becomes the scope's most recent data and the anchor jumps to today, hiding exactly the camp week
the viewer wanted. An event's end date does not drift. **Derive only when the app has nothing better.**

This is three fallbacks in one expression, not three code paths. **If the implementation starts growing
cases beyond that, drop anchoring entirely and return ranges relative to now** — the operator gated
this on staying simple, and a clever version is worse than none.

**E. Retention.** jg has `cleanup_activity_buckets` (`events/management/commands/`). Port it; a
per-user-per-bucket table grows with users × time, and an app with a thousand users will notice. Make
the retention window configurable alongside the bucket width.

**F. Permissions.** Recording is the acting user's own presence. **Reading is not** — an activity chart
shows who was present, which is personal data. Decide and state who may query: scope managers only, by
analogy with `read_receipt_detail` in messaging? `sec_reviewer` rules on this; do not default it open.

## CONTEXT PACKAGE — verified current state (Orchestrator, Implementation map)

Work from this package; do not explore broadly from scratch — open only the named files to verify.
If you must dig deeper, delegate to a read-only Explore sub-agent (Haiku).

**New app: `src/django_core_micha/activity/`**, mirroring the existing `messaging`/`notifications`
app shape — `__init__.py`, `apps.py`, `models.py`, `services.py` (thin views delegate here, matching
messaging's split), `views.py`, `serializers.py`, `urls.py`, `migrations/`, `tests/`,
`management/commands/cleanup_activity_buckets.py` (retention command lives in `management/commands/`
per the `notifications` app's precedent for command placement).

**A. The model — copy `MessagingScope`'s generic-scope shape exactly**
(`src/django_core_micha/messaging/models.py:46-68`, full block):
```python
content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
object_id = models.CharField(max_length=64, null=True, blank=True)   # CharField, not PositiveIntegerField
content_object = GenericForeignKey("content_type", "object_id")
```
`object_id` is deliberately a `CharField(max_length=64)`, not an integer field — it must work for both
int PKs (jg's `Event.id`) and UUID PKs (other consumers) without dcm knowing which. Add an `app`-key
field (a short string identifying the consuming app, e.g. `app_key`) alongside — the WO explicitly
requires "uniqueness including the app key," mirroring `MessagingScope`'s
`UniqueConstraint(fields=["app", "kind", "content_type", "object_id"], ...)` pattern
(`messaging/models.py:61-68`) but for `(app_key, content_type, object_id, user, bucket_start)`.
Index on `(content_type, object_id, bucket_start)` — the read pattern is always scope + bucket range
(`messaging`'s own `Meta.indexes` convention, e.g. `events/models.py:544-546`'s jg-side
`models.Index(fields=["event", "bucket_start"])` is the direct analog, generalized to the FK pair).

**B. Storage granularity — one hour, hard-coded, not configurable.** jg's own bucket-width helper
(`events/activity.py:9`, `ACTIVITY_BUCKET_HOURS = 4`) floors to a fixed boundary — port the *shape* of
that flooring logic (floor `now` to the nearest hour boundary) but with a fixed 1-hour width, no
per-app parameter.

**C. Ping endpoint — port jg's exact accumulation rule**
(`backend/events/views.py:322-361` in the jg-ferien repo, `EventActivityPingView`, full method):
```python
with transaction.atomic():
    last_ping_at = (Model.objects.select_for_update()
        .filter(scope=..., user=request.user, last_ping_at__isnull=False)
        .order_by("-last_ping_at").values_list("last_ping_at", flat=True).first())
    delta_seconds = 0
    if last_ping_at is not None:
        elapsed_seconds = (now - last_ping_at).total_seconds()
        delta_seconds = int(max(0, min(max_credit_seconds, elapsed_seconds)))  # capped, default 45s
    bucket, _created = Model.objects.get_or_create(scope=..., user=request.user, bucket_start=bucket_start)
    Model.objects.filter(pk=bucket.pk).update(
        active_seconds=F("active_seconds") + delta_seconds, last_ping_at=now)
```
Three details that are easy to drop and must be ported exactly: `select_for_update()` (prevents
double-crediting from concurrent tabs), the **capped delta** rather than raw elapsed time (protects
against clock skew / long gaps inflating `active_seconds` — jg's default cap is 45s, keep a
configurable-with-that-default cap), and a final `.update()` with `F()` (atomic increment, not a
read-modify-`.save()` race). The endpoint takes `content_type` (as an app-provided model label, e.g.
`"app_label.ModelName"`) + `object_id` + the `app_key`, resolves/creates the scope row, then applies
the above.

**D. Query endpoint — genuinely new code, no dcm precedent to copy.** Confirmed by grep: dcm has
**zero** existing `TruncHour`/`TruncDay`/`TruncMonth` usage anywhere — this is new. jg's own read
endpoint (`backend/events/views.py:1856-1891`, `EventViewSet.activity`) is the aggregation-shape
reference (`Count("user", distinct=True)`, `Sum("active_seconds")`) but explicitly does **not**
truncate to a coarser granularity — it groups only by the raw stored `bucket_start`, returning one row
per *stored* bucket regardless of requested range. The rollup itself (`django.db.models.functions
.TruncHour/TruncDay/TruncMonth` + `.annotate(bucket=Trunc...(...)).values("bucket").annotate(
distinct_users=Count("user", distinct=True), total_active_seconds=Sum("active_seconds"))
.order_by("bucket")`) must be written from scratch. Reject a request for a granularity finer than the
1-hour store (e.g. an explicit "minute" ask, if such a value were ever accepted) with a clear 400, not
a silent fallback.

**Anchoring** — implement the exact 3-fallback expression from the WO (`supplied_anchor or
MAX(bucket_start) for the scope or now`) as one expression, not branching logic that could grow. If it
starts growing branches, stop and drop anchoring per the WO's own instruction — do not push through.

**E. Retention command** — port `events/management/commands/cleanup_activity_buckets.py` (jg-ferien,
full file, 33 lines) near-verbatim: `--older-than-days` CLI arg (default 365), `bucket_start__lt=cutoff`
filter, bulk `.delete()`, success message with the deleted count.

**F. Permissions — the load-bearing design gap, resolve it explicitly.** Verified: jg's actual
read-permission check (`can_manage_event(request.user, event)`, a jg-local structural helper) is
**not** analogous to messaging's `read_receipt_detail` capability string — it is domain knowledge
(who manages this specific event) that dcm must never learn, per this WO's own core constraint.
**Reusing messaging's exact mechanism is not possible; reuse its *pattern* instead.** Messaging solves
an equivalent problem (a per-app-pluggable permission decision dcm's core cannot itself make) via a
`Protocol`-based policy object the consuming app registers:
`MessagingPolicy` (`messaging/policy.py:27-34`) + `register_messaging_policy(app_key, policy)`
(`policy.py:40`). **Define an equivalent `ActivityPolicy` protocol** (e.g. a single method like
`can_read_activity(*, actor, scope) -> bool`) with a matching `register_activity_policy(app_key,
policy)`, and gate the query endpoint on it. **Default-closed, not default-open**: if no policy is
registered for an `app_key`, deny the read (403), do not fall through to "any authenticated user."
`sec_reviewer` must confirm this default-closed behavior explicitly — it is the exact failure mode the
WO warns against ("do not default it open").

**URL registration** — dcm is a library; each app ships its own flat `urlpatterns` with no namespace
(`messaging/urls.py:9-35`, `notifications/urls.py:16-26` are the precedent). Add
`src/django_core_micha/activity/urls.py` with `path("ping/", ...)` / `path("query/", ...)`, named
consistently (`activity-ping`, `activity-query`, matching `messaging-*`/`notification-*` naming).
Wiring into each consuming app's own `backend/api/urls.py` is explicitly out of scope here (that is
the per-app rewire, analogous to SHELL-1's host WOs).

**Versioning** — single source `pyproject.toml:7` (`[project].version`). Release commit pattern:
`chore(release): bump to X.Y.Z -- publishes <TICKET> (<summary>)`, touching only `pyproject.toml`
(CHANGELOG.md entry goes in the feature commit itself, not the release commit).
`.github/workflows/publish.yml` triggers on push to `main` touching `pyproject.toml` or
`src/django_core_micha/**`, publishes to PyPI only if the version increased (same
compare-against-published-version pattern as ucm's `publish.yml`).

## NON-GOALS / DO NOT TOUCH
- No consumer-specific fields, FKs, or naming. See the constraint above.
- Do not migrate jg's data here — that is jg's WO, which runs after this publishes.
- Do not build the chart. That is `ui-core-micha` CHART-2.
- No metric registry, no configurable aggregation DSL. Two aggregations over raw rows.
- Do not change `MessagingScope` or messaging in any way; borrow the pattern, do not extend the model.

## RISKS
- **Row volume, and it grew.** One row per user per hour per scope — 24/user/day/scope, four times
  what jg writes today at 4-hour buckets. That is the direct cost of the hourly view. Retention
  (scope E) and the read index (scope A) are load-bearing here, not tidy-up.
- **Reading is a privacy surface** — scope F. Getting it wrong exposes per-user presence to anyone who
  can see the scope.
- **Over-generalising from jg.** The design is anchored on three named cases; if a fourth need appears
  mid-implementation, report it rather than widening the model speculatively.
- The generic FK makes queries slightly heavier than a direct FK. Verify the read path stays a single
  aggregate query per request, not a per-row `content_object` resolution.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. Two pings from the same user in the same bucket accumulate into **one** row, not two — the
   uniqueness and upsert behaviour.
2. Pings against two different scope objects of the **same** content type stay separate.
3. The query endpoint rolls up: given fine-grained stored buckets, a coarser requested granularity
   returns the correct summed/counted values, and **the number of returned rows matches the coarse
   granularity, not the stored one.** That row-count assertion is the guard against "aggregation" that
   silently returns raw rows.
4. Distinct-users and presence-time aggregations are correct when one user has several buckets and
   several users share one bucket — the case a naive `COUNT(*)` gets wrong.
5. Retention removes rows outside the window and keeps rows inside it.
6. Scope F: a caller without the read permission gets nothing for a scope they may not read — assert
   the denial, not merely that the happy path works.

**Non-vacuity:** test 3 must fail if roll-up is removed; test 6 must fail if the permission check is
dropped. Prove both by reverting the guard.

## TEST SCOPE FOR THE GATE (orchestrator)
The new activity module's tests only. Not the full suite.

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `develop` if it exists, else `main`.
Publish + version bump per the repo's release flow.

## MINI-HANDOVER (pastable)

> Repo: `C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `develop` if it exists, else
> `main`). Work order: `work-orders/ACT-1.md` — read it fully, then follow the `orchestrate-codex` skill.
>
> **Port jg's working implementation, do not design from scratch.** jg's `EventActivityBucket`
> (`events/models.py:528-545`) is already 90 % generic — exactly one FK is app-specific. Swap it for
> `MessagingScope`'s proven `content_type`/`object_id`/`GenericForeignKey` pattern
> (`messaging/models.py:54-63`) and keep dcm ignorant of what the scope object is.
>
> Three real consumers anchor the design: jg → Event, spesix → School, survey_app → ProjectSite. All
> scope to the host's structural container.
>
> **Two things that are easy to get wrong.** The query endpoint must aggregate **in the database** to a
> requested granularity — the operator wants a 1-year range, which is ~2190 stored buckets per user at
> 4-hour resolution, so returning raw rows is not an option (test 3's row-count assertion pins this).
> And reading activity exposes per-user presence: `sec_reviewer` decides who may query, and it must not
> default open.
>
> Metrics are aggregations over raw rows (`COUNT(DISTINCT user)`, `SUM(active_seconds)`), not stored
> fields — there is no metric registry to build.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
