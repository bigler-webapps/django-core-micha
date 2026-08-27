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

> **PLACEHOLDER — not yet filled.** The Orchestrator fills this on `git pull`. **Do not dispatch
> Codex against this WO while this placeholder stands** — and the Codex preamble block must be
> present in this file before any invocation (`AGENTS.md` → Work Order, two dispatch blockers).

---

## Part C — Orchestrator only

> **STOP — everything below this line addresses the Orchestrator, not the implementer.**
> If you are implementing this work order, your instructions end above.

To be filled per `orchestrate-codex`: Tier-3 routing (`reviewer` + `sec_reviewer`, concurrent) —
shared core, so the review reads the consumer impact as much as the diff. Named review questions:
"which consumer call site still assumes one row per key", and "can an open problem now notify more
than once per cycle". The consumer survey (scope item 4) is part of the work, not a follow-up; its
result belongs in the register Notiz with the per-app counts. Sequence: dcm change and release, then
the pin bump in each consumer, then verification there — the row reaches `done` only when the last
consumer is deployed, per `AGENTS.md`'s end-to-end rule. Commit-on-green to `main`.
