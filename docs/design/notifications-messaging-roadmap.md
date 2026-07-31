# Notifications & Messaging — Target Picture, Must-Reads, Roadmap

Status: **Phase A CLOSED 2026-07-30 · Phase B running.** Layer 1 built and jg fully cut over; the legacy
overlay tables are dropped and no legacy notification producer remains. MSG-1 is **done** (2026-07-31 —
binding design: [`messaging-platform.md`](./messaging-platform.md)); the 2026-07-31 operator revision
makes the build **consumer-agnostic** (jg first via MSG-5, spesix deferred); MSG-2 is **in progress**
(envelope `work-orders/MSG-2.md`, Part B filled; chunk-0 pre-check resolved 2026-07-31 — `filetype`
already distinguishes OOXML/ODF from a bare ZIP via internal member checks, no validator change needed).
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

**Revision (operator, 2026-07-31):** "spesix is the first live greenfield consumer" is superseded — the
platform is built **consumer-agnostic**; jg-ferien is the first intended beneficiary (MSG-5 pulled
forward), spesix is deferred (MSG-4, backlog, no demand recorded). Binding detail:
[`messaging-platform.md`](./messaging-platform.md) (status header + Go/no-go).

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
7. ~~Workspace memory: `project_shared_notifications_platform.md`, `project_messaging_centralization.md`.~~
   **Dead reference, removed 2026-07-30** — neither file exists anywhere (not in the workspace, not in the
   agent memory store, no matching index entry), and the per-repo `MEMORY.md` files in dcm and jg contain
   nothing about notifications either. There is no memory layer for this workstream: the registers and
   these two design docs are the whole record. Do not go looking for a third source.

**Code entry points**
8. dcm notifications: `src/django_core_micha/notifications/` — `api.py` (`notify()`), `router.py`,
   `types.py` (code-first registry), `dispatch.py` (chip/email/push/todo/popup), `todo/`
   (`registry.py`/`service.py`/`digests.py`), `consumers.py` (S112 `NotificationConsumer`), `urls.py`
   (`feed/*`).
9. ucm surfaces: `src/notifications/` — `realtime.jsx` (**the Layer-1 core**: single socket +
   `subscribe(envelope, handler)`), `NotificationsProvider.jsx` (sole mount point, owns the socket and the
   feed state), `NotificationBell.jsx`, `PopupSurface.jsx`, `feedApi.js`, `serviceWorker/sw.js`.
10. jg todo adoption: `backend/events/todo_channel.py` (providers + digest + `build_tasks_from_canonical`),
    `backend/events/views.py` (`/api/tasks/` adapter + the 4 dismiss/override endpoints),
    `backend/events/task_engine.py` — **read this correctly: it is no longer a legacy engine.** Its
    assembly wrapper was deleted (NOTIF-22); what remains is the **live per-task-type rule library**
    (`_payment_items`, `_duty_items`, `_build_task_context`, …) that `todo_channel.py` imports and runs in
    production.
11. jg messaging: `backend/messaging/` (models/services/signals), `messaging/envelope.py`
    (`MESSAGING_ENVELOPE`), `messaging/notifications.py` (the registered new-message type),
    `frontend/src/context/MessagingContext.jsx`, `frontend/src/context/realtimeEnvelopes.js`,
    `frontend/src/components/Messaging/` (`Thread.jsx`, `ConversationList.jsx`, …).
    **`frontend/src/context/NotificationsContext.jsx` no longer exists** — jg's local socket owner was
    deleted in NOTIF-15; do not reintroduce it.

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
| System notifications | dcm 2.35.0: canonical model, `notify()` router, prefs matrix, 5 dispatchers (popup now real), `feed/*`, `NotificationConsumer`, `transient=` + `feed_visible`. ucm 2.14.0: provider/bell/settings/sw.js/popup surface | **NOTIF-14 landed (`37ea0a7`):** the message-notify producer is on `notify()` with a registered type; all six messaging WS payloads tagged `envelope: "messaging"`. **Correction 2026-07-30:** this row previously claimed the type is "rendered per recipient language". The *mechanism* does that, but for this type the effect is nil — its title/body msgids are bare format strings (`"{sender}"`, `"{group}: {sender}"`, `"{excerpt}"`) with `msgid == msgstr` in de/en/fr, so all three languages render identically. Behaviour parity with the pre-cutover code is preserved, which was the actual requirement; there is simply no prose to translate | central; **jg has no legacy notification producer left at all** (the last one went with NOTIF-18, `cbb14e3`) |
| Onboarding | dcm `onboarding` + ucm provider/wizard (dialog shell now shared with the popup channel) | — | fully central |
| Task/todo engine | dcm todo channel (NOTIF-8/8b/8c, 2.32.0) | **NOTIF-10 landed (`e8d5d76`):** all live reads+writes cut over to canonical, dual-write shims retired. The 3 overlay tables still exist but are unread | cutover complete; only the drop (NOTIF-11) remains, gated on the jg promotion |
| Messaging | none | 100% local: 9 models, 25 REST endpoints, event-chat-sync signals, ~2400-LOC `Thread.jsx`, encrypted-at-rest | greenfield centrally — unchanged, this is Phase B |
| Realtime transport | **✓ extracted (NOTIF-13):** ucm `useRealtime()`/`subscribe(envelope, handler)`, unknown envelopes ignored. **One socket for the notifications stream** — verified on staging. Note the invariant is scoped to `/ws/notifications/`, not to the app: jg also runs `/ws/cook/events/…/checklist/` (`BuyChecklistConsumer`, S112-compliant), a separate pre-existing feature and the natural next candidate to ride Layer 1 | **NOTIF-15 landed (`5148677`):** local `NotificationsContext` deleted; `MessagingContext` **and** `Thread.jsx` re-subscribe via Layer 1 | done |
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

### Phase B — messaging v1 (shared full-parity, greenfield, consumer-agnostic) — prefix `MSG-*`
**Revision (operator, 2026-07-31):** built consumer-agnostic — nothing app-specific enters dcm/ucm (app
specifics live behind the provider/policy hooks). **jg-ferien is the first intended beneficiary** (MSG-5,
pulled forward from Phase C); **spesix (MSG-4) is deferred** — backlog, no demand recorded; its demand
gate applies to MSG-4 only, not MSG-2/3.
Starts **after** Phase A closes (Layer 1 extracted — met 2026-07-30). Design validated against two
shapes: jg (reference, full feature set — the one *real* input) + a hypothetical spesix object-thread
shape (paper test only; see §5).
| WO | Repo | Scope |
|---|---|---|
| MSG-1 | dcm(+design) | **Requirements + design doc**: generalize jg's messaging domain (Conversation kinds, Participant/read-state, Message + reactions/polls/attachments, event-chat-sync, WhatsApp-tick receipts) off the `Event` FK onto a generic scope; reconcile against spesix's concrete needs. **Encryption-at-rest key-management for a multi-app service = explicit design-risk block, resolved here.** Rides Layer 1; produces `notify()` for "new message". **✓ done 2026-07-31** (`0b3a47d`/`576c094` → the binding `messaging-platform.md`; "spesix's concrete needs" became a hypothetical paper test per the revision above). |
| MSG-2 | dcm | messaging domain models + services + REST/realtime on the Layer-1 transport (**no new WS consumer** — corrected per design §Realtime; rides `push_to_users`) + `notify()` on new message + `notify(expires_at=…)` API — **envelope authored 2026-07-31** (`work-orders/MSG-2.md`), gated on operator go |
| MSG-3 | ucm | messaging surfaces (Thread/ConversationList/composer/receipts/reactions/polls) — full parity |
| MSG-4 | spesix | spesix adopts the shared service — **deferred 2026-07-31**, backlog (no demand recorded); entry gate = the spesix demand confirmations |

### Phase C — adopters (MSG-5 pulled forward 2026-07-31; rest sketched)
- **MSG-5 (jg)** — migrate jg's existing messaging onto the shared service **including encrypted-at-rest
  content**. **Pulled forward (operator, 2026-07-31): jg is the first intended consumer** — dcm register
  row MSG-5 minted; timing (directly after MSG-3 vs. later) = operator call at MSG-3 end; carries the
  CI-5 production-janitor deploy gate (first production consumer).
- **MSG-6+ (hram/spesix/…)** — additional messaging adopters as needed.

---

## 5. Open design risks
- **Messaging encryption key-management (multi-app).** Full-parity + hard at-rest requirement means the
  shared service must own an encryption scheme that works across tenants/apps without a single shared key
  that widens blast radius. Resolve in MSG-1 before any model lands.
- ~~**Transport extraction backward-compat (NOTIF-13).**~~ **Resolved, and since 2026-07-30 verified live
  on staging — no residual left.** A cross-repo review drove the deployed staging app in a browser and
  exercised chat in **both** directions on real (prod-synced) data:
  - exactly **one** socket, `wss://…/ws/notifications/`, observed on the wire (not just unit-tested);
  - outbound and inbound messages both arrived as `{"envelope": "messaging", "type": "message", …}` and
    rendered live in the thread with no reload;
  - **as the recipient** — i.e. with `notify()` genuinely firing — the bell stayed at `0`,
    `feed/unread-count/` returned `{"count": 0}` and `feed/` was empty. That is the direct proof of
    `feed_visible=False` and of the "bell = system notifications, chat badge = human messages" split;
  - the deployed bundle contains the Layer-1 router verbatim (`t.envelope ?? DEFAULT`, subscriber lookup,
    ignore when nobody subscribes) and no longer contains `registerMessageCallback`;
  - the onboarding wizard still renders correctly (step counter, and the single-step "Einrichtung" variant),
    confirming NOTIF-12's dialog-shell extraction did not regress it across the ucm 2.12 → 2.14 jump.
- **Cross-repo release chains discovered mid-implementation** — the biggest real cost driver so far, not a
  hypothetical. Three times in four days an app-level WO turned out to need a dcm capability that did not
  exist (`transient=`/`feed_visible` in NOTIF-19; `created_by` in NOTIF-8c; `expires_at`, still open as
  NOTIF-21), each forcing a publish → PyPI check → pin bump → redeploy cycle in the middle of the WO. When
  scoping any WO that calls `notify()`, settle four questions **first**: which channels, whether a persistent
  notification row is wanted at all, whether it should be **feed-visible** (independent of the channels!),
  and what may be persisted in `content` and the `dedup_key`. Missing the last two is what caused NOTIF-19.
- **Full-parity abstraction from 2 shapes — sharpened 2026-07-31.** jg is the only *real* input; the
  second shape (spesix object threads) is a hypothetical paper test since the consumer-agnostic revision
  deferred spesix. Rule-of-three risk accepted (operator) with the YAGNI guardrail — generalize to the
  jg-parity floor + the designed seams, not speculatively; revisit when the first non-jg consumer
  materialises.
- **jg data migration (MSG-5 — now the first consumer track).** Encrypted-at-rest content is the hard
  part; since the 2026-07-31 revision this debt is no longer far-term — MSG-5 is the intended first
  adoption, timed by the operator at MSG-3 end.
