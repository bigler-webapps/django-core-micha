# Notifications & Messaging — Target Picture, Must-Reads, Roadmap

Status: **Phase A CLOSED 2026-07-30.** Layer 1 built and jg fully cut over; the legacy overlay tables are
dropped and no legacy notification producer remains. Next up is Phase B (`MSG-*`), starting with MSG-1.
Forward-looking companion to
[`notifications-platform.md`](./notifications-platform.md) (the canonical, approved **notifications**
design). This doc adds: the **must-reads** for anyone picking up the workstream, the **consolidated
3-layer target picture** (notifications **and** messaging), the verified **current state**, and the
**phased roadmap** from here. Where the two docs overlap on notifications internals,
`notifications-platform.md` wins; messaging is defined only here (the platform doc excludes it by design).

Decisions baked in (operator, 2026-07-29): messaging v1 = **full jg-parity**; jg data =
**greenfield (jg keeps its data, migrates later)**, spesix is the **first live greenfield consumer**;
sequencing = **close notifications cleanly first**; encryption-at-rest = **hard v1 requirement** for the
shared messaging service; roadmap horizon = **near phases detailed, far phases sketched**.

---

## 1. Must-reads (in order)

A session picking up this workstream should read these before touching code:

**Governance / process**
1. `AGENTS.md` (repo root) — modes, tiers, approval gates, reviews, finalize, the Work-Order Register.
2. `CLAUDE.md` (repo root) — Claude orchestration split (Expertenchat / Orchestrator / Codex), branch rules.
3. Role skills: `.claude/skills/expert-requirements/SKILL.md`, `.claude/skills/orchestrate-codex/SKILL.md`.

**Design / state (this workstream)**
4. This doc.
5. [`notifications-platform.md`](./notifications-platform.md) — canonical notifications design (D1/D2/D3,
   router, todo channel, phases P1→P3).
6. Registers: **dcm `WORK_ORDERS.md` is the canonical `NOTIF-*` platform register**; app-side execution
   rows live in each app's own `WORK_ORDERS.md` (jg-ferien especially).
7. Workspace memory: `project_shared_notifications_platform.md`, `project_messaging_centralization.md`.

**Code entry points**
8. dcm notifications: `src/django_core_micha/notifications/` — `api.py` (`notify()`), `router.py`,
   `types.py` (code-first registry), `dispatch.py` (chip/email/push/todo/popup), `todo/`
   (`registry.py`/`service.py`/`digests.py`), `consumers.py` (S112 `NotificationConsumer`), `urls.py`
   (`feed/*`).
9. ucm surfaces: `src/notifications/` — `NotificationsProvider.jsx` (single-WS owner), `NotificationBell.jsx`,
   `feedApi.js`, `serviceWorker/sw.js`.
10. jg todo adoption: `backend/events/todo_channel.py` (providers + digest), `backend/events/views.py`
    (`/api/tasks/` adapter + the 4 dismiss/override endpoints), `backend/events/task_engine.py` (legacy
    engine, being retired). jg messaging: `backend/messaging/` (models/services/signals),
    `frontend/src/context/{NotificationsContext,MessagingContext}.jsx`,
    `frontend/src/components/Messaging/` (`Thread.jsx`, `ConversationList.jsx`, …).

---

## 2. Target picture — three layers

Notifications and messaging are **different domains** (system→user, transient, independent-per-recipient
vs. conversational, co-authored, durable-owned, shared threads + read-receipts). They are **not** merged.
They share **transport** and cross-produce signals ("new message" → a notification).

```
Layer 1 — Shared realtime transport (ucm provider + dcm consumer)
    one WS, pluggable subscribers by message-type
        │  carries ▼                         │  carries ▼
Layer 2 — Notifications domain            Layer 3 — Messaging domain
    (dcm canonical model + router +           (dcm Conversation/Message/Participant/
     notify() + feed/* + todo channel;         receipts + ucm chat surfaces)
     ucm bell/settings)                        rides Layer 1; PRODUCES notify()
```

- **Layer 1 — transport. ✓ Built (NOTIF-13, ucm 2.13.0 / dcm 2.33.0).** ucm's `NotificationsProvider`
  now owns the socket through a generic `useRealtime()` / `subscribe(envelope, handler)` primitive
  (`src/notifications/realtime.jsx`); jg's local `NotificationsContext` is deleted (NOTIF-15). Messages
  are routed by an **envelope discriminator that names the domain** (`envelope: "notification"`,
  `envelope: "messaging"`), and an unknown envelope is ignored rather than mistaken for a notification.
  A payload with no `envelope` defaults to `"notification"`, so an un-bumped backend keeps working.
  **Correction to the original plan:** it called for "a dcm consumer that carries multiple stream types".
  That was never needed — `delivery.py::push_to_users` already fans arbitrary payloads into the per-user
  group and `NotificationConsumer.message()` is stream-agnostic. The whole problem lived in ucm's
  client-side catch-all. dcm's share was the envelope contract plus the `notification_envelope()` helper.
- **Layer 2 — notifications.** Canonical model, router, `notify()`, category×channel prefs, dispatchers,
  `feed/*`, todo channel, bell/settings. Design: `notifications-platform.md`. Remaining closure work in §4.
- **Layer 3 — messaging.** A shared full-parity chat subsystem (Conversation/Message/Participant/
  read-receipts + reactions/polls/attachments/event-chat-sync generalized off jg's `Event` FK), riding
  Layer 1 and emitting `notify()` for "new message". **greenfield**: spesix is the first live consumer;
  jg keeps its local data and migrates in a later phase. Encryption-at-rest is a hard v1 requirement.

Dependency direction is strictly downward: Layer 3 depends on Layer 1; Layer 2's frontend also depends on
Layer 1. Nothing in Layer 1 depends on 2 or 3.

---

## 3. Current state (verified 2026-07-30)

| Domain | Central (dcm/ucm) | Local in jg | Verdict |
|---|---|---|---|
| System notifications | dcm 2.35.0: canonical model, `notify()` router, prefs matrix, 5 dispatchers (popup now real), `feed/*`, `NotificationConsumer`, `transient=` + `feed_visible`. ucm 2.14.0: provider/bell/settings/sw.js/popup surface | **NOTIF-14 landed (`37ea0a7`):** the message-notify producer is on `notify()` with a registered type rendered per recipient language; all six messaging WS payloads tagged `envelope: "messaging"` | central; **jg has no legacy notification producer left at all** (the last one went with NOTIF-18, `cbb14e3`) |
| Onboarding | dcm `onboarding` + ucm provider/wizard (dialog shell now shared with the popup channel) | — | fully central |
| Task/todo engine | dcm todo channel (NOTIF-8/8b/8c, 2.32.0) | **NOTIF-10 landed (`e8d5d76`):** all live reads+writes cut over to canonical, dual-write shims retired. The 3 overlay tables still exist but are unread | cutover complete; only the drop (NOTIF-11) remains, gated on the jg promotion |
| Messaging | none | 100% local: 9 models, 25 REST endpoints, event-chat-sync signals, ~2400-LOC `Thread.jsx`, encrypted-at-rest | greenfield centrally — unchanged, this is Phase B |
| Realtime transport | **✓ extracted (NOTIF-13):** ucm `useRealtime()`/`subscribe(envelope, handler)`, one socket, unknown envelopes ignored | **NOTIF-15 landed (`5148677`):** local `NotificationsContext` deleted; `MessagingContext` **and** `Thread.jsx` re-subscribe via Layer 1 | done |
| Retention | `prune_notifications` exists (NOTIF-4) | — | **deliberately not scheduled** (NOTIF-20/21 dropped 2026-07-30) — no benefit at these volumes, and `transient=` already keeps chat text out of `content`. Reactivatable if a consumer ever produces high volume |
| Scheduled commands | role `scheduled_commands` is carried by **`staging` only** — CI-3's documented staging-first gate | jg declares `send_todo_digests`, the estate's only scheduled command | so **no app command has ever run in production**; the completion step is `webapp-management/work-orders/CI-5.md` |

---

## 4. Roadmap

Canonical register = dcm `WORK_ORDERS.md`; app-side rows in app registers. Each dcm/ucm phase is a release
train (publish-from-main, **registry live-check before app pin-bump**). `[approval]` = schema migration on
a production model.

### WO-numbering reconciliation (two collisions, both resolved)
1. **2026-07-29:** splitting the old single-step NOTIF-10 into **NOTIF-10 (cutover)** + **NOTIF-11 (drop)**
   (jg) collided with the platform register's **NOTIF-11 = popup channel**. Resolved: **popup → NOTIF-12**
   (uncut/future, annotated "was NOTIF-11").
2. **2026-07-29, same day:** an unplanned dcm schema fix was filed as **NOTIF-13**, which this document had
   already allocated to the transport extraction — but §4's IDs had never been mirrored into the register,
   so the "highest used ID" check could not see them. Resolved: the schema fix became **NOTIF-8c** (it
   extends the NOTIF-8/8b `TodoOverride` line), and NOTIF-13..17 were registered immediately.

**Lesson, and the standing rule now:** an ID that exists only in this document does not exist. Every WO
named here must have a register row in dcm `WORK_ORDERS.md` at the moment it is named, even with no WO file.

### Phase A — close notifications cleanly (Layer 2 done + Layer 1 extracted)
| WO | Repo | Scope | Depends on | Status |
|---|---|---|---|---|
| NOTIF-9 | jg | adopt dcm todo channel (dual-write expand) | NOTIF-8 | ✓ done (bb0580b) |
| NOTIF-10 | jg | **read/write cutover** to canonical-only (derive/digest reads + 4 live endpoints; retire dual-write shims); tables stay | NOTIF-9 | ✓ done (`e8d5d76`) |
| NOTIF-11 | jg | **drop** the 3 legacy overlay tables (guarded, one-way) `[approval]` | NOTIF-10 promoted to **prod** + no-residual-access proof | ✓ done (`bec2a0e`) — migration `0063`, tables confirmed absent via `information_schema` |
| NOTIF-12 | ucm+dcm | popup channel via the shared dialog shell (was NOTIF-11) | NOTIF-13 | ✓ done (ucm `c8e222f` / dcm `1612429`) — **ships with zero producers, deliberately** |
| NOTIF-13 | ucm+dcm | **Layer-1 transport extraction** — the hinge to messaging | NOTIF-6 | ✓ done (ucm `7a83ee9` / dcm `de77335`) |
| NOTIF-14 | jg | **message-notify → `notify()`** + tag the six messaging WS payloads `envelope: "messaging"` | — | ✓ done (`37ea0a7`) |
| NOTIF-15 | jg | **jg bell/feed adoption:** ucm provider + bell; delete jg's local `NotificationsContext` | NOTIF-13 **and NOTIF-14** | ✓ done (`5148677`) |
| NOTIF-16 | hram | hram `notify()` adoption (state-only "job done") | Layer 2 stable | planned — **backlog, no demand recorded** |
| NOTIF-17 | spesix | spesix `notify()` adoption (state-only "job done") | Layer 2 stable | planned — **backlog, no demand recorded** |
| NOTIF-18 | jg | retire the **unscheduled** legacy task-digest producer (a deletion, not a migration) | — | ✓ done (`cbb14e3`) — last legacy `deliver_push_email` producer in jg is gone |
| NOTIF-22 | jg | move the deep task coverage onto the canonical path, retire `build_tasks_for_user` | NOTIF-11 | ✓ done (`1f52c92`, `7442794`) — **TE-2 duty pin HELD on the canonical path**, so no hidden NOTIF-9/10 regression |
| NOTIF-19 | dcm | `notify(transient=…)` + `NotificationType.feed_visible` | raised by NOTIF-14 | ✓ done (`bea6ad0`, 2.35.0) |
| NOTIF-20 | jg+cockpit | schedule the `prune_notifications` janitor | — | **dropped** 2026-07-30 — no benefit at these volumes; surfaced the `scheduled_commands` role gap instead (→ `CI-5`) |
| NOTIF-21 | dcm+jg | per-type retention: expose `expires_at` on `notify()` | NOTIF-20 | **dropped** 2026-07-30 — moot without NOTIF-20; the API gap itself is real and recorded |

**Ordering — corrected.** The original note here claimed *"NOTIF-14 is independent (backend delivery) and
can land any time"*. **That was wrong.** jg's six messaging WS payloads are chat live-sync, not
notifications, so they must never go through `notify()` — but they were envelope-less, and Layer 1 defaults
an envelope-less payload to the notification envelope. Retiring jg's local socket owner without tagging them
first would have poured the entire chat stream into the notification feed. The tagging therefore lives in
NOTIF-14, and **NOTIF-15 depends on it**. The rest holds: NOTIF-13 precedes NOTIF-15; NOTIF-16/17 are
parallelizable per-app tracks.

**Phase A is closed.** NOTIF-11 dropped the three legacy overlay tables (`bec2a0e`) and NOTIF-22 moved the
deep task coverage onto the canonical path and retired the last dead wrapper (`1f52c92`). NOTIF-22 also settled
the one open correctness question the cutover had left: the TE-2 pin — a cancelled registration whose
`GroupMembership` survives must still surface its duty task — **holds on the canonical path**, so NOTIF-9/10
hid no regression behind the legacy harness.

**Still open, deliberately:** NOTIF-16/17 (hram/spesix `notify()` adoption) remain backlog with no demand
recorded — nothing is broken in either app without them. They are not a Phase A prerequisite.

**One thing left the workstream, deliberately.** Investigating NOTIF-20 revealed that the platform's
`scheduled_commands` role is granted to `staging` only, so no app command has ever executed in production —
CI-3's intended staging-first gate, still awaiting its documented completion. That is a platform concern,
not a notifications one, and lives as `webapp-management/work-orders/CI-5.md`. It matters here only because
jg's `send_todo_digests` — the digest NOTIF-8/9/10 cut over to — is the single command it would switch on.

### Phase B — messaging v1 (shared full-parity, greenfield, spesix-first) — prefix `MSG-*`
Starts **after** Phase A closes (Layer 1 extracted). Co-design from **two real shapes**: jg (reference,
full feature set) + spesix (first live consumer).
| WO | Repo | Scope |
|---|---|---|
| MSG-1 | dcm(+design) | **Requirements + design doc**: generalize jg's messaging domain (Conversation kinds, Participant/read-state, Message + reactions/polls/attachments, event-chat-sync, WhatsApp-tick receipts) off the `Event` FK onto a generic scope; reconcile against spesix's concrete needs. **Encryption-at-rest key-management for a multi-app service = explicit design-risk block, resolved here.** Rides Layer 1; produces `notify()` for "new message". |
| MSG-2 | dcm | messaging domain models + services + S112 WS consumer (on Layer-1 transport) + `notify()` on new message |
| MSG-3 | ucm | messaging surfaces (Thread/ConversationList/composer/receipts/reactions/polls) — full parity |
| MSG-4 | spesix | spesix adopts the shared service (first live greenfield consumer) |

### Phase C — far-term (sketched)
- **MSG-5 (jg)** — migrate jg's existing messaging onto the shared service **including encrypted-at-rest
  content** (the hard migration deliberately deferred; jg runs local until then).
- **MSG-6+ (hram/spesix/…)** — additional messaging adopters as needed.

---

## 5. Open design risks
- **Messaging encryption key-management (multi-app).** Full-parity + hard at-rest requirement means the
  shared service must own an encryption scheme that works across tenants/apps without a single shared key
  that widens blast radius. Resolve in MSG-1 before any model lands.
- ~~**Transport extraction backward-compat (NOTIF-13).**~~ **Resolved.** The primitive carries both stream
  types, an envelope-less payload still defaults to the notification envelope, and jg's chat re-subscribed
  without loss. Residual: end-to-end chat over the socket was never browser-verified (no local
  events/conversations, no seed command) — it rests on unit coverage of all nine handler paths and an exact
  envelope match on both sides. Exercise chat once on staging before the jg promotion.
- **Cross-repo release chains discovered mid-implementation** — the biggest real cost driver so far, not a
  hypothetical. Three times in four days an app-level WO turned out to need a dcm capability that did not
  exist (`transient=`/`feed_visible` in NOTIF-19; `created_by` in NOTIF-8c; `expires_at`, still open as
  NOTIF-21), each forcing a publish → PyPI check → pin bump → redeploy cycle in the middle of the WO. When
  scoping any WO that calls `notify()`, settle four questions **first**: which channels, whether a persistent
  notification row is wanted at all, whether it should be **feed-visible** (independent of the channels!),
  and what may be persisted in `content` and the `dedup_key`. Missing the last two is what caused NOTIF-19.
- **Full-parity abstraction from 2 shapes.** jg + spesix are the only real inputs; rule-of-three risk is
  accepted (operator) with the YAGNI guardrail — generalize to jg+spesix needs, not speculatively.
- **jg data migration (Phase C).** Encrypted-at-rest content is the hard part; greenfield-first buys time
  but the migration debt is real and must not be silently forgotten.
