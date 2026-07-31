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

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit. This is a
**documentation-only** WO — no application code changes anywhere. The only file this WO creates is
`docs/design/messaging-platform.md` in `django-core-micha`, plus the `WORK_ORDERS.md` row update to
`in-review`/`done`.

### Target repo / working directory

`C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root — this repo has no `backend/`/
`frontend/` split; it is the shared platform package).

### Context package

**Named file to produce:**
- `docs/design/messaging-platform.md` — new file, companion to `docs/design/notifications-platform.md`.
  Follow that file's structure/tone/heading style (skim it first) as the sibling document.

**Source material to read (all read-only pointers, already verified to exist):**
- `docs/design/notifications-platform.md` (471 lines) — sibling doc, mirror its structure/section
  conventions (headings, contract-table style, decision framing).
- `docs/design/notifications-messaging-roadmap.md` (218 lines) — §2 three-layer picture, §4 Phase B,
  §5 open risks; this WO exists to close the gaps this file flags.
- `src/django_core_micha/notifications/` — api/router/types/dispatch/delivery/consumers, `todo/`
  (the existing shared notification service MSG-2 will extend — read to understand `notify()`,
  `transient=`, `feed_visible`, channel/type registration).
- `../jg-ferien/backend/messaging/models.py` (249 lines) — the 9-model jg-parity floor to generalize
  (`Conversation.event` mandatory FK to loosen into the generic scope model; kinds
  broadcast/event_all/event_team → policy hooks).
- `../jg-ferien/backend/messaging/services.py` (935 lines) — business logic: read-tick semantics, DM
  privacy carve-out, mute, unread counts, reactions, polls, moderation.
- `../jg-ferien/backend/messaging/views.py` (874 lines) — the ~21 REST endpoints to enumerate in the
  new contract (paths, payloads, permissions, cursor pagination — jg's pagination may be a gap per
  deliverable #8, note explicitly if so).
- `../jg-ferien/backend/messaging/envelope.py` (5 lines) — the `messaging` envelope registration
  pattern (Layer-1 seam).
- `../jg-ferien/backend/messaging/notifications.py` (61 lines) — the exact `notify()` recipe to make
  normative (deliverable #5): type registration, `email`+`push` only, no `chip`,
  `feed_visible=False`, sender/muted exclusion, `transaction.atomic()`.
- `../jg-ferien/backend/messaging/event_chat_sync.py` (110 lines) — `event_all`/`event_team` signal
  sync as the precedent for the managed-membership provisioning hook (deliverable #3).
- `../jg-ferien/backend/messaging/fields.py` (101 lines) — `MultiFernet` encryption-at-rest precedent
  (deliverable #4).
- `../jg-ferien/backend/messaging/urls.py` (55 lines) — endpoint list cross-check against views.py.
- `../jg-ferien/frontend/src/components/Messaging/` (`Thread.jsx` ~2.5k LOC, `ConversationList.jsx`,
  `MessagingConfig.jsx`, `NewDirectMessageDialog.jsx`, `AnnouncementDialog.jsx`,
  `EmojiPickerButton.jsx`, `conversationHelpers.js`) + `context/MessagingContext.jsx` +
  `context/realtimeEnvelopes.js` (paths relative to `../jg-ferien/frontend/src/`) — component cut
  reference for deliverable #8 (ucm surface architecture).
- `../ui-core-micha/src/notifications/realtime.jsx`, `NotificationsProvider.jsx`, `PopupSurface.jsx`
  — Layer-1 `useRealtime().subscribe(envelope, handler)` pattern; note the documented gotcha
  (subscribe via the destructured `subscribe`, never the `useRealtime()` object).
- This repo's `WORK_ORDERS.md` (register) and `../jg-ferien/WORK_ORDERS.md` (jg messaging history:
  WO-MSG-1, MSG-2/3, MSG-B1..B4, MSG-UX, SEC-2, PERF-3D2, NOTIF-14/15) — read for precedent framing
  only, do not edit jg-ferien's register.

**Invariants / do-not-touch:**
- No code changes anywhere (this repo, jg-ferien, ui-core-micha, spesix) — documentation only.
- Do not weaken the encryption-at-rest requirement (deliverable #4) to make the doc easier to write —
  if no clean multi-app key scheme exists, the doc must say so explicitly as a STOP with options,
  not silently pick a weaker scheme.
- Do not resolve the Go/No-Go gate yourself — write the confirmation questions into the doc as an
  open gate for the operator; do not assume the answer beyond the envelope's stated assumption (all
  three spesix forms wanted) and say clearly that this is an assumption pending confirmation.
- Keep the two paper tests (jg shape, spesix shape) as literal validation sections inside the
  document, worked through against the model actually proposed — not just named as a to-do.
- Do not touch `WORK_ORDERS.md` rows for MSG-2/MSG-3/MSG-4 beyond what's already there (placeholders
  stay placeholders; this WO does not author their envelopes).

**Known pitfalls:**
- jg's `Conversation.event` FK is mandatory today — the doc must show precisely how the generic scope
  model (container / object-anchor / app-global) subsumes this without losing jg's event-scoped DM
  case (Phase C parity, deliverable #2's explicit requirement).
- The four contract questions (channels / persistent row / `feed_visible` / `content`+`dedup_key`
  safety) must be answered for **every** notification-producing event type in the new contract, not
  just message — reactions, polls, edits/deletes, delivered/read-state if added.
- Volume/retention (deliverable #6) needs actual estimated numbers, not just a qualitative note —
  reference NOTIF-20/21's dropped-at-current-volume decision and state what changes the calculus.

### Required tests

None (documentation WO, per the envelope). Validation = the two paper tests written into the document
itself.

### Progress contract

Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (file opened,
section drafted) and `PROGRESS: [<n>/<total>] done` on step completion, spaced so no gap exceeds
~2 min, stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.

### Preamble (must be appended verbatim to the Codex prompt)

The text above is the COMPLETE spec — read nearest `AGENTS.md`, `.codex/skills/<role>/SKILL.md` (if
present), and this repo's `MEMORY.md` only for conventions; stay in scope; do not touch application
code, auth/permissions/deps/schema/CI; do not update `MEMORY.md`; do NOT `git add`/`commit`/`push` —
leave the new file uncommitted in the working tree for the orchestrator's independent review. Write
the design document only. Do not run any test suite (none required) and do not run any review — the
orchestrator runs the independent review after you finish.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-1.md` in `django-core-micha` (main). `git pull` first, read
the WO, then follow `orchestrate-codex` (Codex-first, own review, commit on green).
