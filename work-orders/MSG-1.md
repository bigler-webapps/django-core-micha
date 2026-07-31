# MSG-1 — Messaging v1: requirements + design document

Status: planned · Tier 2 (documentation WO, no code) · Target repo: `django-core-micha`
Canonical register row: this repo's `WORK_ORDERS.md`. Roadmap context: `docs/design/notifications-messaging-roadmap.md` (§2 three-layer picture, §4 Phase B, §5 open risks).

---

## Part A — Envelope (Expertenchat, 2026-07-31)

### Goal

Produce the binding requirements + design document for the shared messaging domain — target file
`docs/design/messaging-platform.md` (companion to `notifications-platform.md`) — complete enough that
MSG-2 (dcm domain), MSG-3 (ucm surfaces) and MSG-4 (spesix adoption) can be built **without discovering
missing contracts or capabilities mid-implementation**. Mid-WO cross-repo release chains (publish → PyPI
check → pin bump → redeploy inside a running WO) were the NOTIF campaign's biggest real cost driver
(3 incidents in 4 days; roadmap §5) — this WO exists to prevent a repeat.

### Why now

Phase A closed 2026-07-30. Layer 1 (shared realtime transport) is built and production-proven: one WS,
`useRealtime().subscribe(envelope, handler)` in ucm, `envelope: "messaging"` already carries jg's live
chat (NOTIF-13/14/15). Messaging v1 is greenfield centrally: jg keeps its local implementation until
Phase C; spesix is the first live consumer.

### Deliverable — the design doc MUST cover

1. **Domain model** — the full jg-parity floor plus the v1 extensions (see "Decided v1 feature scope"),
   every model and field named; the four schema seams are part of the v1 schema even where behaviour/UI
   comes later.
2. **Generic scope model** — conversations anchored to (a) an app **container scope** (jg: Event),
   (b) a **domain object** (spesix: expense claim — the new object-thread kind; participants derived
   from the object), or (c) **app-global** (spesix DMs). DMs must support scoped AND global. jg's
   event-scoped DMs must remain expressible (Phase C parity).
3. **Policy / membership hook interface** — app-supplied hooks for: who may DM whom, who may post in a
   conversation, live recipient resolution, moderation rights (edit/delete beyond author), and
   managed-membership provisioning (jg's `event_all`/`event_team` signal sync becomes a provider;
   spesix's object-thread participant derivation uses the same seam). Exact signatures and contracts —
   these are the MSG-2/MSG-4 build surface.
4. **Encryption-at-rest key management (multi-app)** — hard v1 requirement. No single shared key across
   apps; rotation supported (jg's `MultiFernet` is the precedent); provisioning via the sanctioned
   `sync-secrets` path; applies to message bodies/titles/poll content AND attachments. If no clean
   multi-app scheme exists, STOP and return options to the operator — never weaken this silently.
5. **Complete API + realtime contract** — every REST endpoint (paths, payloads, permissions, cursor
   pagination) and every WS event type on the `messaging` envelope (jg today: `message`,
   `message_edited`, `message_deleted`, `reaction`, `poll_updated` — extend deliberately, e.g.
   `delivered`/read-state updates). For **every** notification-producing event, answer the four contract
   questions up front: which channels? persistent row at all? `feed_visible`? what may enter
   `content`/`dedup_key` (anything sensitive goes through `transient=`)? jg's proven recipe is
   normative: type registered per app, `email`+`push` only, no `chip`, `feed_visible=False`, recipients
   pre-filtered by domain logic (sender excluded, muted excluded), `notify()` inside
   `transaction.atomic()`.
6. **Notification volume / retention re-assessment** — NOTIF-20/21 were dropped 2026-07-30 at
   *current* volumes; a shared messaging service changes the picture (one `Notification` +
   `NotificationRecipient` per message per recipient). State, with estimated numbers, whether janitor
   scheduling and/or per-type TTL (`notify(expires_at=…)` — API gap recorded in NOTIF-21's register row)
   return to scope, and where (dcm API vs. deploy config; note the `scheduled_commands` staging-only
   gate, `webapp-management/work-orders/CI-5.md`).
7. **Attachment pipeline** — document base-set allowlist (PDF + common Office formats + images) via
   magic-byte validation (dcm `validators.upload.validate_upload`), size cap ~25 MB, encrypted at rest
   with the same key regime as message content, delivery download-only through authenticated
   decrypt-and-stream views (no inline rendering of foreign content, no generic `/media/` exposure —
   jg's blocked-urlconf pattern is the precedent). Images keep jg's re-encode/EXIF-strip/thumbnail
   pipeline. **Scan-hook interface defined, NO scanner infrastructure in v1.**
8. **ucm surface architecture — full parity in v1** (operator decision 2026-07-31, deliberately not
   spesix-scoped): component cut for ConversationList / Thread / Composer / read-tick rendering /
   reactions / polls / attachments; **cursor pagination + infinite scroll and optimistic send from day
   one** (both are known jg gaps, deliberately fixed in the shared build); i18n de/en/fr; PWA/reconnect
   behaviour = Layer-1 semantics (subscribe via the destructured `subscribe`, never the `useRealtime()`
   object — documented ucm gotcha).
9. **Phase C migration sketch** — jg data migration path at sketch level only (encrypted-content
   re-keying, feature-parity checklist, event-scoped conversations mapping onto the generic scope).
   No implementation planning beyond the sketch.
10. **Chunk + release plan for MSG-2/3/4** — broad WOs with many chunks are explicitly wanted (operator
    preference 2026-07-31). Define: the dcm → ucm → spesix version chain (publish-from-main, registry
    live-check before any app pin bump); staged per-chunk commits permitted (NOTIF-7 precedent) instead
    of one monster diff; **small sibling-repo contract-fix chunks are in-scope for MSG-2/3** (NOTIF-13
    pattern) so a mid-flight contract gap never mints a new WO ID; review cadence per AGENTS.md
    multi-chunk rules (scoped tests per chunk, one independent review per WO on the assembled diff —
    or explicitly staged reviews per commit, NOTIF-7-style, if the chunk plan says so).

### Decided v1 feature scope (normative — operator scoping session 2026-07-31)

**IN — jg-parity floor:** conversation kinds direct / group / broadcast / managed; reactions;
1-level reply threads; polls (single/multi, live counts, named voters, close); image attachments
(re-encode, EXIF strip, encrypted, auth-streamed); edit (author-only) / soft-delete (author or
moderator); read ticks with aggregated `all_read` + moderator-only per-recipient detail and the **DM
privacy carve-out** (never per-recipient detail on DMs); mute; unread counts (per conversation +
global); encryption at rest with rotation; audited break-glass read (dcm auditlog); `notify()`
coupling; system messages (nullable sender); per-scope messaging config (DM policy, group-chat enable,
everyone-can-post).

**IN — v1 extensions beyond jg:** object-thread kind (GenericFK anchor + participant derivation via the
membership hook); delivered-status watermark (separate from read); retention fields (default policy
"never delete"); per-conversation channel prefs (email/push per conversation, beyond the mute boolean);
conversation archiving (participant flag); file attachments per deliverable #7.

**Defaults (write into the design unless the doc surfaces a hard reason against):** read-receipt
semantics exactly as jg; NOTIF-17 (spesix `notify()` adoption) folds into MSG-4; i18n de/en/fr
throughout; DMs both scoped and global per deliverable #2.

**OUT — documented non-goals:**
- **Search** — conflicts with encryption-at-rest (no DB full-text over ciphertext); not even a v1.x
  candidate without its own design effort.
- **Typing indicator** — named v1.x item; designated variant REST-ping + existing fan-out, explicitly
  NOT a client→server WS path.
- **Any client→server WS path** — all writes stay REST (jg precedent; keeps the S112 surface minimal).
- **Malware-scanning infrastructure** — hook only (deliverable #7).
- **Cross-app messaging** — apps have separate user bases; there is no cross-app case.
- **jg data migration** — Phase C (sketch only, deliverable #9).
- **Any code change to dcm/ucm/apps in this WO** — documentation only (register maintenance excepted).

### Go/No-Go gate (part of this WO)

Formally confirm spesix demand: the concrete anchor object (expense claim?), participant derivation
rule, which of the three forms (object threads / global DMs / broadcast) ship in MSG-4, and the
timeline. Envelope assumption from the scoping session: all three forms wanted. **If confirmation
fails, MSG-1 ends with the design shelved — a documented outcome, not a failure**; MSG-2..4 stay
`planned` or become `dropped` by operator decision.

### Paper tests (validation inside the doc — mandatory)

1. **jg shape:** every current jg messaging feature expressed in the new model without loss — including
   event-scoped DMs, managed kinds via the provisioning hook, encrypted image attachments, the DM
   read-status carve-out, and the exact `notify()` recipe.
2. **spesix shape:** one concrete expense-claim object thread + one app-global DM + one broadcast, end
   to end — participants/permissions from the policy hooks, notification flow through the contract.

### Required tests to write

None — documentation WO (deliberate, per AGENTS.md "omit only if genuinely no test is needed"):
validation = the two paper tests above + an independent reviewer pass on the document (author ≠
reviewer, Tier 2) + operator acceptance at Approval Gate #1 before MSG-2.

### Risks

- Key management may have no clean multi-app answer → hard stop, options back to the operator.
- spesix confirmation may change the shapes → envelope change goes back to the operator (never a silent
  edit by the Orchestrator).
- Full-parity MSG-3 is the largest v1 block; the chunk plan must be honest (Phase B total realistically
  ~10–14 chunks across MSG-2/3/4, not 3 rows' worth).
- Volume: unbounded `Notification` rows per message — addressed by deliverable #6, must not be dropped
  again silently.

### Preconditions / dependencies

Phase A closed (met 2026-07-30). No dcm/ucm publish involved in this WO (doc-only; no version bump).

### Source material (read-only pointers for the implementing session)

- jg reference implementation: `jg-ferien/backend/messaging/` (models/services/views/urls,
  `envelope.py`, `notifications.py`, `event_chat_sync.py`, `fields.py`),
  `jg-ferien/frontend/src/components/Messaging/` + `context/MessagingContext.jsx` +
  `context/realtimeEnvelopes.js`. Facts: 9 models, ~21 REST endpoints, 5 WS event types, **no own WS
  consumer, no client→server WS**; frontend ~6k LOC incl. tests (`Thread.jsx` ~2.5k). Deliberately
  generic already (register rows MSG-B1..B4: no Event imports): reactions, attachments, polls,
  thread-read. Hard-wired to jg: `Conversation.event` mandatory FK (even DMs), kinds
  broadcast/event_all/event_team, `can_dm`/`can_post`/recipient logic on
  `EventMembership`/`GroupMembership`, `event_chat_sync.py` signals.
- Platform: `src/django_core_micha/notifications/` (api/router/types/dispatch/delivery/consumers,
  todo/), `docs/design/notifications-platform.md`, `docs/design/notifications-messaging-roadmap.md`.
- ucm Layer 1: `ui-core-micha/src/notifications/realtime.jsx` (+ `NotificationsProvider.jsx`,
  `PopupSurface.jsx` as the two existing subscribers).
- Registers: this repo's `WORK_ORDERS.md` (canonical MSG-*), `jg-ferien/WORK_ORDERS.md` (jg messaging
  history: WO-MSG-1, MSG-2/3, MSG-B1..B4, MSG-UX, SEC-2, PERF-3D2, NOTIF-14/15).

---

## Part B — Implementation map (Orchestrator)

_To be filled by the Orchestrator on `git pull`, within this envelope: context package, progress
contract, execution directive, mini-handover. The envelope above is authoritative WHAT/WHY; scope
changes go back to the operator._
