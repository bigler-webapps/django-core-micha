# Notifications & Messaging — Target Picture, Must-Reads, Roadmap

Status: **planning** (2026-07-29). Forward-looking companion to
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

- **Layer 1 — transport.** Today ucm's `NotificationsProvider` owns one socket but is hardwired to
  notification message-types; jg works around this with its **own** local `NotificationsContext` that
  multiplexes (`registerMessageCallback`) so `MessagingContext` can ride it. **Target:** promote that
  multiplexing into ucm as a generic realtime primitive (one socket, subscribe by message-type) + a dcm
  consumer that carries multiple stream types; retire jg's local context. This is the **hinge**: it
  finishes the notifications frontend (jg adopts the ucm bell) **and** is the substrate messaging needs.
- **Layer 2 — notifications.** Canonical model, router, `notify()`, category×channel prefs, dispatchers,
  `feed/*`, todo channel, bell/settings. Design: `notifications-platform.md`. Remaining closure work in §4.
- **Layer 3 — messaging.** A shared full-parity chat subsystem (Conversation/Message/Participant/
  read-receipts + reactions/polls/attachments/event-chat-sync generalized off jg's `Event` FK), riding
  Layer 1 and emitting `notify()` for "new message". **greenfield**: spesix is the first live consumer;
  jg keeps its local data and migrates in a later phase. Encryption-at-rest is a hard v1 requirement.

Dependency direction is strictly downward: Layer 3 depends on Layer 1; Layer 2's frontend also depends on
Layer 1. Nothing in Layer 1 depends on 2 or 3.

---

## 3. Current state (verified 2026-07-29)

| Domain | Central (dcm/ucm) | Local in jg | Verdict |
|---|---|---|---|
| System notifications | dcm 2.31.0: canonical model, `notify()` router, prefs matrix, 5 dispatchers, `feed/*`, `NotificationConsumer`. ucm 2.12.0: provider/bell/settings/sw.js | jg's **message-notify** producer (`messaging/services.py`) still calls legacy `deliver_push_email`/`push_to_users`, NOT `notify()` | Delivery central; jg's 2nd producer NOT on the canonical router |
| Onboarding | dcm `onboarding` + ucm provider/wizard | — | fully central |
| Task/todo engine | dcm todo channel (NOTIF-8/8b, 2.29.0) | **NOTIF-9 landed (bb0580b):** providers registered, overlays dual-written to canonical, `/api/tasks/` dcm-derive-backed via adapter. Legacy engine + 3 overlay tables still LIVE (dual-write expand) | data-side centralized; contract phase = NOTIF-10 (cutover) + NOTIF-11 (drop) |
| Messaging | none | 100% local: 9 models, 25 REST endpoints, event-chat-sync signals, ~2400-LOC `Thread.jsx`, encrypted-at-rest | greenfield centrally |
| Realtime transport | ucm provider = notifications-only, not pluggable | jg-local `NotificationsContext` multiplexes the one socket for messaging | not extracted |

---

## 4. Roadmap

Canonical register = dcm `WORK_ORDERS.md`; app-side rows in app registers. Each dcm/ucm phase is a release
train (publish-from-main, **registry live-check before app pin-bump**). `[approval]` = schema migration on
a production model.

### WO-numbering reconciliation (fix a collision introduced 2026-07-29)
Splitting the old single-step NOTIF-10 into **NOTIF-10 (cutover)** + **NOTIF-11 (drop)** (jg) collided with
the platform register's **NOTIF-11 = popup channel**. Resolution: **popup → NOTIF-12** (it is uncut/future;
annotate "was NOTIF-11", precedent: it was already "was NOTIF-10"). Action item: update dcm
`WORK_ORDERS.md` row 38 + `notifications-platform.md` P3 accordingly. jg's pushed NOTIF-10/11 are unchanged.

### Phase A — close notifications cleanly (Layer 2 done + Layer 1 extracted)
| WO | Repo | Scope | Depends on | Status |
|---|---|---|---|---|
| NOTIF-9 | jg | adopt dcm todo channel (dual-write expand) | NOTIF-8 | ✓ done (bb0580b) |
| NOTIF-10 | jg | **read/write cutover** to canonical-only (derive/digest reads + 4 live endpoints; retire dual-write shims); tables stay | NOTIF-9 | planned |
| NOTIF-11 | jg | **drop** the 3 legacy overlay tables (guarded, one-way) `[approval]` | NOTIF-10 staging-green + no-residual-access proof | planned |
| NOTIF-12 | ucm | popup channel via wizard renderer (was NOTIF-11) | NOTIF-6 | planned (P3, uncritical) |
| NOTIF-13 | ucm+dcm | **Layer-1 transport extraction:** generalize ucm provider to pluggable-by-message-type + dcm multi-stream consumer; keep S112 + single-socket invariant. The hinge to messaging. | NOTIF-6 | planned |
| NOTIF-14 | jg | **message-notify → `notify()`:** move jg's 2nd producer (`messaging/services.py`) off legacy `deliver_push_email`/`push_to_users` onto the canonical router + a registered type | — (backend delivery; independent) | planned |
| NOTIF-15 | jg | **jg bell/feed adoption:** frontend onto ucm `NotificationsProvider`/`NotificationBell` on `feed/*`; retire jg-local `NotificationsContext` (messaging re-subscribes via Layer 1) | NOTIF-13 | planned |
| NOTIF-16 | hram | hram `notify()` adoption (state-only "job done") | Layer 2 stable | planned |
| NOTIF-17 | spesix | spesix `notify()` adoption (state-only "job done") | Layer 2 stable | planned |

Ordering notes: NOTIF-13 (transport) precedes NOTIF-15 (bell live-updates + retiring jg's local context).
NOTIF-14 is independent (backend delivery) and can land any time. NOTIF-16/17 are per-app adoption tracks,
parallelizable once Layer 2 is stable.

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
- **Transport extraction backward-compat (NOTIF-13).** Retiring jg's local `NotificationsContext` must not
  drop messaging's live updates; the pluggable primitive must carry both stream types before jg cuts over.
- **Full-parity abstraction from 2 shapes.** jg + spesix are the only real inputs; rule-of-three risk is
  accepted (operator) with the YAGNI guardrail — generalize to jg+spesix needs, not speculatively.
- **jg data migration (Phase C).** Encrypted-at-rest content is the hard part; greenfield-first buys time
  but the migration debt is real and must not be silently forgotten.
