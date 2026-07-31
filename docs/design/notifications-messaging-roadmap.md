# Notifications & Messaging — Target Picture, Must-Reads, Roadmap

Status: **Phase A CLOSED 2026-07-30 · Phase B running.** Layer 1 built and jg fully cut over; the legacy
overlay tables are dropped and no legacy notification producer remains. MSG-1 is **done** (2026-07-31 —
binding design: [`messaging-platform.md`](./messaging-platform.md)); the 2026-07-31 operator revision
makes the build **consumer-agnostic** (jg first via MSG-5, spesix deferred).

**Phase B backend and surfaces are built (state 2026-08-01): dcm 2.38.0 · ucm 2.18.0.** MSG-2 shipped the
domain and MSG-3 the surfaces; a post-completion three-way audit then found substantial parity and
contract gaps on both sides, closed across four follow-up WOs — MSG-2b, MSG-2c, MSG-2d (dcm) and MSG-3b,
MSG-3c (ucm). **Everything the audit found is delivered** — MSG-3d closed the last two rows on 2026-08-01 (ucm 2.19.0),
so `messaging-deviations.md` carries zero BLOCKED entries and **MSG-5 is unblocked**. §5 records the
systemic causes — they are the most reusable thing in this document. The platform still has **zero
consumers**: nothing in the estate exercises the messaging domain until MSG-5 lands, and MSG-5 should
not start while rows 27/42 are open.
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
| System notifications | dcm 2.36.0: canonical model, `notify()` router, prefs matrix, 5 dispatchers (popup now real), `feed/*`, `NotificationConsumer`, `transient=` + `feed_visible`, `expires_at=` (MSG-2 closed the NOTIF-21 gap). ucm 2.15.0: provider/bell/settings/sw.js/popup surface | **NOTIF-14 landed (`37ea0a7`):** the message-notify producer is on `notify()` with a registered type; all six messaging WS payloads tagged `envelope: "messaging"`. **Correction 2026-07-30:** this row previously claimed the type is "rendered per recipient language". The *mechanism* does that, but for this type the effect is nil — its title/body msgids are bare format strings (`"{sender}"`, `"{group}: {sender}"`, `"{excerpt}"`) with `msgid == msgstr` in de/en/fr, so all three languages render identically. Behaviour parity with the pre-cutover code is preserved, which was the actual requirement; there is simply no prose to translate | central; **jg has no legacy notification producer left at all** (the last one went with NOTIF-18, `cbb14e3`) |
| Onboarding | dcm `onboarding` + ucm provider/wizard (dialog shell now shared with the popup channel) | — | fully central |
| Task/todo engine | dcm todo channel (NOTIF-8/8b/8c, 2.32.0) | **NOTIF-10 landed (`e8d5d76`):** all live reads+writes cut over to canonical, dual-write shims retired. The 3 overlay tables still exist but are unread | cutover complete; only the drop (NOTIF-11) remains, gated on the jg promotion |
| Messaging | **dcm 2.36.0 (MSG-2): the full Layer-3 domain** — models, per-app fail-closed keyrings, `MessagingPolicy` hooks, services, REST + `messaging` frames (no new consumer), attachments, `notify(expires_at=…)`. **ucm: nothing yet (MSG-3)** | 100% local: 9 models, 25 REST endpoints, event-chat-sync signals, ~2400-LOC `Thread.jsx`, encrypted-at-rest | **built centrally, zero consumers.** jg is untouched and stays on its local stack until MSG-5; the shared domain is unexercised by any app until then |
| Realtime transport | **✓ extracted (NOTIF-13):** ucm `useRealtime()`/`subscribe(envelope, handler)`, unknown envelopes ignored. **One socket for the notifications stream** — verified on staging. Note the invariant is scoped to `/ws/notifications/`, not to the app: jg also runs `/ws/cook/events/…/checklist/` (`BuyChecklistConsumer`, S112-compliant), a separate pre-existing feature and the natural next candidate to ride Layer 1 | **NOTIF-15 landed (`5148677`):** local `NotificationsContext` deleted; `MessagingContext` **and** `Thread.jsx` re-subscribe via Layer 1 | done |
| Retention | `prune_notifications` exists (NOTIF-4) | — | **deliberately not scheduled** (NOTIF-20/21 dropped 2026-07-30) — no benefit at these volumes, and `transient=` already keeps chat text out of `content`. Reactivatable if a consumer ever produces high volume |
| Scheduled commands | **CI-5 done 2026-07-31:** `scheduled_commands` now on `main-prod` too, and the nightly cron is verified firing there from the job's own log (run `30610666474`) | jg declares `send_todo_digests`, the estate's only scheduled command | **runs in production — but on 0 candidates, because prod's `main` predates NOTIF-22.** Same night, same data, staging (`develop`) scanned 40 users and sent 1 digest. The `develop→main` promotion flips prod to real digest email; the observed first run is jg `OPS-1` |

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

**One thing left the workstream — and came back closed (2026-07-31).** Investigating NOTIF-20 revealed that
the platform's `scheduled_commands` role was granted to `staging` only, so no app command had ever executed
in production — CI-3's intended staging-first gate. That was a platform concern, not a notifications one,
and lived as `webapp-management/work-orders/CI-5.md`; it is now **done**: the role is on `main-prod` and the
nightly cron is verified running jg's `send_todo_digests` there against `jg_prod_backend`.

It still matters here, for a reason CI-5's own measurements did not show. Prod scanned **0** users while
staging, on the same prod-synced data the same night, scanned **40** and sent a digest. The difference is
jg application code, not the platform: prod's `main` is 27 commits behind `develop` and predates NOTIF-22's
canonical-path task coverage. So the digest machinery this workstream built is running in production but has
never actually produced a user-facing email — and the jg `develop→main` promotion is the moment it starts.
That first run belongs under observation rather than under the unattended cron; it is tracked as jg `OPS-1`.

### Phase B — messaging v1 (shared full-parity, greenfield, consumer-agnostic) — prefix `MSG-*`
**Revision (operator, 2026-07-31):** built consumer-agnostic — nothing app-specific enters dcm/ucm (app
specifics live behind the provider/policy hooks). **jg-ferien is the first intended beneficiary** (MSG-5,
pulled forward from Phase C); **spesix (MSG-4) is deferred** — backlog, no demand recorded; its demand
gate applies to MSG-4 only, not MSG-2/3.
Starts **after** Phase A closes (Layer 1 extracted — met 2026-07-30). Design validated against two
shapes: jg (reference, full feature set — the one *real* input) + a hypothetical spesix object-thread
shape (paper test only; see §5).
**One prerequisite sits outside the `MSG-*` prefix:** `ui-core-micha` `DX-1` — a minimal Vite dev
harness, because ucm had no way to render any component at all (only `build` + `test`). Dev-only, no
publish, envelope `ui-core-micha/work-orders/DX-1.md`. It runs before MSG-3 and is not messaging work.

| WO | Repo | Scope |
|---|---|---|
| MSG-1 | dcm(+design) | **Requirements + design doc**: generalize jg's messaging domain (Conversation kinds, Participant/read-state, Message + reactions/polls/attachments, event-chat-sync, WhatsApp-tick receipts) off the `Event` FK onto a generic scope; reconcile against spesix's concrete needs. **Encryption-at-rest key-management for a multi-app service = explicit design-risk block, resolved here.** Rides Layer 1; produces `notify()` for "new message". **✓ done 2026-07-31** (`0b3a47d`/`576c094` → the binding `messaging-platform.md`; "spesix's concrete needs" became a hypothetical paper test per the revision above). |
| MSG-2 | dcm | messaging domain models + services + REST/realtime on the Layer-1 transport (**no new WS consumer** — corrected per design §Realtime; rides `push_to_users`) + `notify()` on new message + `notify(expires_at=…)` API. **✓ done 2026-07-31, published dcm 2.36.0** (chunks `7df9670`/`858f705`/`a6a4cf5`/`3ad7709`, publish `1d8c60d`; independent review per chunk, chunk 3 twice after the operator design calls on `app_key` tenant resolution + soft-delete redaction — see `messaging-platform.md` §"Tenant resolution and deletion semantics"). Its one open P3 (scoped DM forecloses first contact) is decided and cut as **MSG-2b** — see §5 |
| MSG-2b | dcm | **scoped first-contact DM must be possible** — drop `DirectConversationView`'s participant-existence precondition; target-side tenant safety rests on `MessagingPolicy.can_open_direct` alone. Tier 1, no migration. Depends on MSG-2; **planned, runs before MSG-3.** Envelope `work-orders/MSG-2b.md` |
| MSG-3 | ucm | messaging surfaces (Thread/ConversationList/composer/receipts/reactions/polls) — full parity. **Envelope authored 2026-07-31** in the target repo (`ui-core-micha/work-orders/MSG-3.md`); 5 chunks, carries `ui_reviewer`. Operator decisions: **redesign permitted** for layout/interaction with "No jg feature is lost" still binding and a written deviation list as a deliverable; UI validated through the new ucm dev harness. Preconditions: dcm 2.36.1 published · ucm `DX-1` done. **✓ done 2026-07-31, published ucm 2.16.0** (5 chunks, review per chunk, `ui_reviewer` clean, 139/139). Decomposition held (largest component 110 LOC). **A post-completion audit found substantial gaps → `MSG-2c` + `MSG-3b`** |
| MSG-2c | dcm | **contract gaps found after MSG-3:** implement the poll **read** path (the design gap is closed — see `messaging-platform.md` §"Poll read contract and conversation preview", 2026-07-31), emit the 8 missing realtime frames (`services.py` has 3 `_publish` call sites; `attachment_ready` is now recorded as reserved-and-unemitted, so 8 not 9), add `last_message` to the conversation payload. Additive, no schema change. Envelope `work-orders/MSG-2c.md`. **✓ done 2026-07-31, published dcm 2.37.0** (`1399d05`) — independent `reviewer` caught a real P1 (`open_direct` re-broadcast `conversation_upsert` on every reopen of an existing DM; fixed by gating on the `created` flag, plus a matching no-op guard on reactions, both with regression tests). **This unblocks rows 38, 51-53 and 56-58 of MSG-3b** |
| MSG-3b | ucm | **parity + contract gaps found after MSG-3:** unread lifecycle (mark-read was never called — badges never clear), edit/delete, DM launcher, `reply_to_id`-vs-`reply_to` reply grouping, quoting, copy, image compression, and a contract-conformance test. 59-row capability checklist **is** the spec. Envelope `ui-core-micha/work-orders/MSG-3b.md`. **✓ done 2026-07-31, published ucm 2.17.0** — 6 chunks, review per chunk, WO-end `ui_reviewer` row-by-row, no findings. Nine rows left BLOCKED: seven of them wrongly (dcm 2.37.0 had shipped mid-flight → `MSG-3c`), two correctly (→ `MSG-2d`) |
| MSG-2d | dcm | **the two read-side gaps MSG-2c did not cover:** readable thread reply state (`reply_count`/`last_reply_at` viewer-independent, `thread_last_read_at` REST-only) and `external_key` on the conversation payload. Additive, no schema change. Envelope `work-orders/MSG-2d.md`. **✓ done 2026-08-01, published dcm 2.38.0** (`8acf894`) — Codex ran out of credits, so the orchestrator implemented as the governed fallback and became author, making the independent review mandatory rather than routine; it earned its keep, catching a P1 where `.annotate()` silently drops `Meta.ordering` (GROUP BY suppresses it) — a real ~1/3 flake that would have shipped as undefined row order on Postgres. 467/467. **Unblocks MSG-3b rows 27 and 42** |
| MSG-3c | ucm | **the seven rows MSG-3b blocked against a stale dcm version** (38, 51-53, 56-58): last-message preview + reorder, full poll rendering, and the `reaction`/`poll_updated` handlers that had never run against a real frame. Plus the deviation-doc correction and the version-pin rule. Envelope `ui-core-micha/work-orders/MSG-3c.md`. **✓ done 2026-08-01, published ucm 2.18.0** — the never-exercised handlers were indeed where the bugs were: three latent defects of one shape (a viewer-independent projection wiping viewer-specific state on merge), now a contract obligation in the design doc. Rows 27/42 re-verified against dcm's source, correctly still BLOCKED at 2.37.0 — freed by 2.38.0 the same day |
| MSG-3d | ucm | **the last two blocked rows** (27, 42): unread-reply marker on the thread toggle, and `external_key` surfaced so a host can distinguish managed conversations — without ucm learning jg's vocabulary. Envelope `ui-core-micha/work-orders/MSG-3d.md`. **✓ done 2026-08-01, published ucm 2.19.0** — deviation doc now has **zero** BLOCKED entries. **The post-audit remediation is closed and MSG-5 is unblocked** |
| MSG-4 | spesix | spesix adopts the shared service — **deferred 2026-07-31**, backlog (no demand recorded); entry gate = the spesix demand confirmations |

### Phase C — adopters (MSG-5 pulled forward 2026-07-31; rest sketched)
- **MSG-5 (jg) — split into 5a/5b/5c/5d on 2026-08-01.** Envelopes live in the jg repo
  (`jg-ferien/work-orders/MSG-5{a,b,c,d}.md`). A single overnight single-repo WO is not possible, for
  four independent reasons: the pin bump (jg is 3 dcm and 4 ucm minors behind) lands ~11 tables in a
  production database; jg's messaging Fernet key must be created in Proton by the operator before
  `register_messaging_app` can succeed, which it fails closed without, by design; the migration decrypts
  and re-encrypts real user content; and retiring jg's local app is an approval-gated 40-file deletion.
  The shape is NOTIF-9/10/11's — **5a** expand (additive, nothing cut over), **5b** migrate + reconcile,
  **5c** wire the frontend behind a default-off flag (**the night-runnable block**), **5d** flip and
  retire. The legacy-table drop stays a separate guarded step after 5d, exactly as NOTIF-11 followed
  NOTIF-10. All four carry a hard rule: read dcm/ucm, never modify them — every adoption-adjacent WO so
  far surfaced a platform gap, and an unattended session must surface it rather than patch across repos.
  Carries the CI-5 production-janitor deploy gate (first production consumer; CI-5 is now closed).
  **Design correction found while scoping:** the Phase C sketch below says "Preserve IDs" — it cannot be
  done. jg uses integer `AutoField` primary keys and the platform uses `UUIDModel`, so 5b must mint new
  UUIDs and carry a durable old→new mapping table; without it, reply re-pointing, FK translation,
  resumability and idempotency all fail.
- **MSG-6+ (hram/spesix/…)** — additional messaging adopters as needed.

---

## 5. Open design risks
- ~~**Scoped first-contact DM is currently impossible.**~~ **Decided 2026-07-31, cut as `MSG-2b`.**
  `DirectConversationView` required the target to already be a participant somewhere in the resolved app,
  so a scoped DM could only be *continued*, never *started* — and jg's `NewDirectMessageDialog` opens DMs
  against arbitrary event members, so jg parity would have failed on first contact at MSG-5. Operator
  decision: first contact **must** be possible. Resolution: the participant-existence check goes, and
  target-side tenant safety rests solely on `MessagingPolicy.can_open_direct` — which `open_direct()`
  already calls before creating any row, so the check was not only too strict but sat in front of the hook
  and pre-empted it. Core keeps tenant resolution and self-DM rejection. Sequenced **before MSG-3** so the
  ucm composer is not built against behaviour that would change at adoption.
- ~~**CI has never run a single messaging test.**~~ **Fixed 2026-07-31 — dcm `DX-2` (`65db4af`).**
  `testpaths` now covers `messaging/tests`, and a guard test fails naming any
  `src/django_core_micha/*/tests` directory left out of it, with a second test proving the guard is not
  vacuous by reproducing the exact stale config that shipped. Reviewer verified the negative case by
  reverting the fix and watching the guard go red; full CI-mirrored suite 461/461, no cross-module
  interference. **Kept here as the record of what it cost:** the messaging subsystem shipped across
  three work orders without its tests ever entering the promotion gate. The finding below stands.
- **A second local-vs-CI divergence, found while fixing the first — documented, not fixed.** CI's
  `publish.yml` exports `PYTHONPATH=.` and runs `pip install -e ".[test]"` before its bare `pytest -q`.
  A plain local `pytest -q` cannot even import `tests.settings` without that — measured on `main`
  2026-07-31, it dies with `ImportError: No module named 'tests'` followed by pytest-django's
  "could not find a Django project" — while `python -m pytest -q` collects all 461, because `-m` puts
  the working directory on `sys.path` itself. So "run `pytest -q`" means two different things depending
  on who types it, and a green local run is not evidence about CI. Nothing
  is broken today, but this is the same shape of gap as the `testpaths` allowlist: an invisible
  precondition that makes local and CI results silently non-comparable. Worth closing the next time
  anything touches the test entrypoint.
- **Superseded, kept for the trail — the original finding (2026-07-31, pre-DX-2).**
  `pyproject.toml:39` sets `testpaths = ["tests", "src/django_core_micha/notifications/tests",
  "src/django_core_micha/onboarding/tests"]` — an allowlist that was never extended when the
  `messaging` subpackage was added in MSG-2. CI runs a bare `pytest -q`, which collects **401 tests and
  zero of the 58 messaging tests**; they are only reachable by passing the path explicitly, which is
  what every local "messaging suite green" figure across MSG-2 / MSG-2b / MSG-2c actually did. So the
  promotion gate does not cover the messaging subsystem at all, and has not since it was created. It
  also explains a symptom seen in MSG-2c: an implementer running plain `pytest` sees no messaging tests
  and can reasonably conclude none are expected. The fix is one line; it is filed as dcm `DX-2` rather
  than applied in passing because it changes what CI executes, and the first run may legitimately go
  red — which would itself be the finding. Root cause worth fixing beyond the one line: `testpaths` is
  an allowlist with no guard, so the next subpackage will repeat this silently.
- **The version-pin rule worked on its first outing (2026-08-01).** MSG-3c re-verified the two remaining
  BLOCKED rows against dcm's committed source rather than the WO text, recorded the result *with the
  version* — "blocked as of dcm 2.37.0 … re-verified against 2.37.0: still absent" — and even noticed
  dcm had uncommitted `MSG-2d` work in flight. dcm 2.38.0 has since shipped it, and because the pin is
  there, seeing that rows 27 and 42 are now deliverable is a ten-second read instead of an
  investigation. Cheap rule, immediate payoff; keep it on every cross-repo WO.
- **Client-side field-name drift: two occurrences, still unguarded.** `reply_to` vs the server's
  `reply_to_id` (MSG-3b) and `last_message.body` vs the server's `.excerpt` (MSG-3c) are the same defect:
  the client reads a key the server never emits, tests pass because the fixtures are client-shaped, and
  the feature is silently inert. MSG-3b's contract-conformance test closed the *export* and *frame-type*
  gaps but not this one — it compares names of things, not the shape of payloads. Candidate fix, not yet
  cut: have dcm emit a machine-readable contract snapshot (example payloads from the real serializers)
  that ucm's tests assert against, so a renamed or absent field fails the build on the client side.
  Until then the mitigation is the rule MSG-3c already applies — **fixtures must be server-shaped**,
  copied from actual serializer output, never hand-written.
- **Third occurrence, one layer up: a BLOCKED row is only as true as the day it was written.** MSG-3b
  started against dcm 2.36.1, recorded seven rows as blocked on a "future dcm work order", and closed
  with those rows undone — while dcm 2.37.0 (MSG-2c) had shipped exactly that work **mid-flight**. Six
  per-chunk reviews and a WO-end `ui_reviewer` row-by-row cross-check all passed, because the check read
  *ucm's* landed code and took the dependency's state from the WO text instead of reading dcm. Same
  shape as the two findings above, moved one level out: the seam between a WO and its dependency was
  assumed rather than verified. **Rule now:** a BLOCKED row is re-verified against the live dependency
  **at WO close, not at WO start**, and every BLOCKED entry names the dependency version it was blocked
  at. MSG-3b already did the second half unprompted, which is the only reason this cost two minutes to
  find rather than surfacing at MSG-5 — the pin is what makes the re-check cheap. Delivered as `MSG-3c`.
- **Both sides of the seam were tested against themselves — and every gate passed anyway.** The single
  most important lesson of this workstream so far. MSG-2 shipped 146/146 green with four independent
  chunk reviews; MSG-3 shipped 139/139 green with five chunk reviews and a clean `ui_reviewer`. A
  three-way audit afterwards found: an entire missing capability area (unread never clears), two
  features whose REST adapters had no caller (edit/delete, DM launcher), reply grouping that is inert
  because the client reads `reply_to` while the server emits `reply_to_id`, a conversation preview
  bound to a field the serializer never sends, a poll UI reading fields no endpoint returns, and 9 of
  12 designed realtime frames that are never emitted. **None of it was catchable by either side's
  tests**, because dcm tested its services and ucm tested its components against a mock whose surface
  mirrors the *provider* rather than the *contract* — an endpoint with no caller is structurally
  invisible to it. DX-1's envelope predicted exactly this ("a mock adapter can drift from the real
  contract — shape fixtures from the design doc's REST/realtime sections, not from imagination") and
  the prediction came true anyway. The fix is mechanical, not diligence-based: the contract-conformance
  test in MSG-3b (an `api.js` export with no caller, or a handled frame the design does not list,
  fails the build). Apply the same seam check to any future cross-repo contract in this estate.
- **A parity requirement is unverifiable until someone writes the parity list.** MSG-3 made "no jg
  feature is lost" binding and delegated the proof — a deviation list — to the party doing the work,
  against a **file-level** inventory. Files pass as "represented" far too easily; capabilities do not.
  The list came back claiming completeness and was wrong on edit, delete, DM launcher, mark-read,
  quoting, copy and image compression. MSG-3b replaces it with a 59-row capability checklist authored
  before implementation. Do this for MSG-5 too, and for any future adoption WO that claims parity.
- **MSG-5 is now a visible change for jg users (operator decision 2026-07-31).** MSG-3 was granted a
  redesign licence for layout, composition and interaction — jg's current messaging UI is no longer a
  verbatim visual target. The feature floor is unchanged ("No jg feature is lost" stays binding, and
  MSG-3 must ship a written deviation list), but the adoption stops being a silent swap: jg users will
  see a different chat surface. MSG-5 therefore needs its own UX review, and the deviation list is the
  input to it. The counter-risk to watch: "redesign permitted" is easy to over-read as licence to
  reimplement approximately, which is exactly how a feature disappears with nobody deciding to drop it.
- **Every ucm surface ever shipped was verified without ever being rendered.** Scoping MSG-3 surfaced
  that `ui-core-micha` has no Storybook, no demo app and no dev page — only `build` (tsc) and `test`
  (vitest). Auth, onboarding, notifications and charts were all validated in jsdom, or after a consuming
  app pinned them. `DX-1` closes this for MSG-3 and everything after it, but it is worth naming what it
  implies about the surfaces already in production: their visual and responsive behaviour was never
  checked in this repo. NOTIF-13's real defects were found by driving staging in a browser, not by unit
  tests.
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
