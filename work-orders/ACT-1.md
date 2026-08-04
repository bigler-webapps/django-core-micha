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

**Anchoring (operator, 2026-08-04 — keep it simple or drop it).** A range relative to *now* is useless
for a finished scope: a camp that ran in July shows an empty "last 7 days" when opened in August. So
anchor the range's end at **the scope's most recent bucket with data**, falling back to now when the
scope has none.

That is deliberately one rule with no per-app logic and no heuristics. **If the implementation starts
growing cases, drop the anchoring and return ranges relative to now** — the operator gated this on it
staying simple, and a clever version is worse than none.

**E. Retention.** jg has `cleanup_activity_buckets` (`events/management/commands/`). Port it; a
per-user-per-bucket table grows with users × time, and an app with a thousand users will notice. Make
the retention window configurable alongside the bucket width.

**F. Permissions.** Recording is the acting user's own presence. **Reading is not** — an activity chart
shows who was present, which is personal data. Decide and state who may query: scope managers only, by
analogy with `read_receipt_detail` in messaging? `sec_reviewer` rules on this; do not default it open.

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
