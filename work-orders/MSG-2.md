# MSG-2 — dcm messaging domain (models, hooks, contracts, attachments)

Status: planned · Tier 2 `[approval schema]` (additive-only new tables) · Target repo: `django-core-micha` (main)
**Binding spec:** `docs/design/messaging-platform.md` (MSG-1, independently reviewed). This envelope scopes
the WO; on any conflict the design doc wins. Deviations from the design are an operator scope change,
never a silent edit.

---

## Part A — Envelope (Expertenchat, 2026-07-31)

### Goal

Implement the dcm side of the shared messaging domain exactly as designed — a new
`django_core_micha.messaging` subpackage. **Consumer-agnostic** (operator re-ordering 2026-07-31):
nothing app-specific enters dcm; app specifics live behind the provider/policy hooks. jg-ferien is the
first intended beneficiary (MSG-5); spesix is deferred (MSG-4) — the design doc's spesix demand gate does
NOT apply to this WO.

### Expected outcome

- New subpackage `django_core_micha.messaging`: the design's domain model (`MessagingApp` registry,
  `MessagingScope`, `Conversation`, `ConversationParticipant`, `Message`, `MessageReaction`,
  `MessageAttachment`, `MessageThreadReceipt`, `Poll`/`PollOption`/`PollVote`, `MessagingAuditEvent`),
  **additive-only** migrations.
- `MessagingPolicy` hook protocol + app registration with **fail-closed keyring registration** per design
  §Encryption (`MESSAGING_KEYRINGS[app_key]`, MultiFernet, rotation procedure; STOP clause live: if rings
  cannot be provisioned via `sync-secrets` without cross-app exposure, stop and return the design's three
  options to the operator — never a shared key).
- Services: send / edit / soft-delete, reactions, polls, thread receipts, read + delivered watermarks,
  mute / archive / per-conversation channel prefs, unread counts, live recipient resolution, break-glass
  read writing `MessagingAuditEvent` (including denials).
- REST contract per design §REST (signed opaque cursors 50/100, `Idempotency-Key` +
  `client_request_id`, 404-not-member vs. 403-capability), realtime frames per design §Realtime
  (`envelope: "messaging"`, `event_id` dedup, commit-before-fan-out; **NO new WS consumer, NO
  client→server WS** — fan-out via existing `push_to_users`).
- `notify()` integration per design §Notification contract, including the NEW dcm API
  `notify(expires_at=…)` (closes the NOTIF-21 gap) and the documented 30-day-TTL registration recipe for
  the per-app messaging type (registration itself stays per-app).
- Attachment pipeline per design §Attachments, gated on the chunk-0 pre-check outcome.
- **One** version bump + PyPI publish at WO end (no consumer pins in this WO; registry live-check before
  any later pin bump).

### Chunk plan (staged commits, one independent review per chunk — operator decision 2026-07-31)

0. **Pre-check — resolved 2026-07-31, no separate chunk needed.** Orchestrator inspected the
   installed `filetype==1.2.0` matcher source directly (`OfficeOpenXml.match_document` /
   `Odt`'s equivalent, via `type(t).__mro__[1]` for the `docx`/`odt` type instances): it does NOT
   rely on the outer `PK\x03\x04` signature alone — it walks the ZIP's first several local file
   headers and checks internal member names/paths (`[Content_Types].xml`, `_rels/.rels`,
   `docProps`, and the OOXML/ODF-specific entries beyond that) before returning a
   docx/xlsx/pptx/odt/ods/odp match, and a bare `zip`/`epub` is a distinct matcher that only fires
   when those document-specific checks fail. So `validators.upload.validate_upload` already gets a
   reliable OOXML/ODF-vs-archive distinction for free from `detect_mime` — no `validators/upload.py`
   change and no new dependency needed. Chunk 4 just needs to pass the correct
   `allowed_mimes` set (design §Attachments' PDF/OOXML/ODF/image list) into the existing
   `validate_upload(file_obj, allowed_mimes=..., max_size=...)` call; the caveat added to the
   design doc stands as documented reasoning, not as an open implementation gap.
1. Models + migrations + `MessagingApp`/keyring registration (fail-closed; `sync-secrets` declaration
   pattern documented). Highest-risk chunk — its review must include a security pass on the crypto path.
2. Policy hooks + services (incl. break-glass + audit).
3. REST + realtime (endpoints, serializer safety — no plaintext leaks into logs/errors, permissions,
   frames).
4. Attachments + `notify(expires_at=…)` + TTL/janitor compatibility.

### Required tests to WRITE (scoped; the security set is mandatory — auth/permission/crypto surface)

- **Keyrings:** fail-closed registration (missing/malformed/empty/shared ring), rotation re-encrypt
  round-trip, no plaintext fallback on decrypt failure.
- **Permissions/IDOR:** per endpoint class — non-member → 404 (no existence oracle), capability failure
  → 403; DM read-status carve-out (never per-recipient detail, including moderators); moderation
  capability matrix (`edit_any`/`delete_any`/`read_receipt_detail`/`manage_config`/`open_broadcast`/
  `open_group`/`create_managed`).
- **Recipient resolution:** sender + muted excluded from `notify()`; live re-resolution before every
  send; provider membership upsert / remove-absent semantics.
- **Notification recipe:** `feed_visible=False` respected by `feed/*` + unread-count; `transient=` values
  never persisted into `content` nor `dedup_key`; dedup `(type, notifiable)`; delivery failure cannot
  roll back the durable message.
- **Idempotency:** `client_request_id` unique-retry + `Idempotency-Key` POST retry yield exactly one
  message.
- **Pagination:** opaque signed cursor, bad cursor → 400, default/max 50/100.
- **Attachments:** allowlist accepts PDF/OOXML/ODF/images and rejects bare ZIP, HTML/SVG, executables,
  MIME-mismatch and polyglots; image re-encode + EXIF strip; encrypted at rest (blob + thumbnail +
  filename); download-only disposition + `nosniff`; `scan_state` defaults `unscanned`; hook invoked when
  configured.
- **TTL:** `expires_at` honored by `prune_notifications`; messaging-notification TTL never touches
  `Message`/`Conversation` rows.
- **Realtime:** frames carry envelope/`event_id`; emission only to policy-resolved live users;
  `test_ws_inventory` unchanged (assert NO new consumer was added).
- WO-end gate: affected-area run = full notifications + messaging suites (existing notifications
  behaviour unchanged).

### Non-goals / do-not-touch

ucm (MSG-3); any app adoption or app-specific code (jg/spesix); scanner infrastructure (hook only);
search; typing; client→server WS; jg data migration (MSG-5); **no dependency changes without prior
operator approval**; no behavioural change to existing notifications beyond the additive `expires_at`
API; `django_core_micha.auth` core untouched — messaging enforcement lives in the new subpackage's
policy layer.

### Risks

- Crypto/keyring handling (chunk 1) — staged security-inclusive review is the mitigation.
- ~11 new tables in one WO — additive-only keeps rollback trivial while no consumer exists.
- Contract drift vs. the design doc — the doc is binding; deviations surface to the operator.
- OOXML/ODF detection may prove unreliable (chunk 0) — decision back to the operator (accept residual
  risk vs. drop Office formats from the allowlist), never silently widened.

### Preconditions

MSG-1 done + independently reviewed (met 2026-07-31). Approval Gate #1 for MSG-2 = operator go on this
envelope. Design §Go/no-go explicitly does not gate this WO (operator re-ordering 2026-07-31).

### Execution note

This is a **code WO — Codex-first applies** (unlike MSG-1, which was doc-only). Staged per-chunk commits,
each with its own independent review (operator decision 2026-07-31); the Orchestrator runs scoped tests
per chunk + the affected-area set at WO end; publish once at WO end with registry live-check.

---

## Part B — Implementation map (Orchestrator)

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.
**One `codex exec` invocation per chunk** (0 through 4, chunk 0 may fold into chunk 4 if trivially
resolved per the envelope). Each chunk's prompt is this same file's Part A + Part B **plus a
chunk-specific instruction block** appended at invocation time naming which chunk to build and
reminding it not to start the next chunk. Each chunk lands as its own commit after its own
independent review (operator decision — staged reviews, not one WO-end review).

### Target repo / working directory

`C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root — no `backend/`/`frontend/` split;
this is the shared PyPI package `django_core_micha`, current version `2.35.0` in `pyproject.toml`).

### Context package

**New subpackage to create:** `src/django_core_micha/messaging/` — mirror the sibling
`src/django_core_micha/notifications/` subpackage's layout and conventions exactly:
`__init__.py`, `apps.py` (label `django_core_micha_messaging`), `models.py`, `migrations/`,
`serializers.py`, `views.py`, `urls.py`, `services.py` (or split `services/` package — Codex's
call, jg's `messaging/services.py` is 935 lines and a single-file split is acceptable precedent),
`policy.py` (the `MessagingPolicy` Protocol + registry), `crypto.py` (keyring/MultiFernet, mirrors
jg's `fields.py`), `notifications.py` (type registration, mirrors jg's `messaging/notifications.py`
and dcm's own `notifications/types.py`), `consumers.py` — **do not create one**; design §Realtime
is explicit there is no new WS consumer, fan-out reuses `notifications.delivery.push_to_users`,
`admin.py`, `tests/` (mirror `notifications/tests/` naming: `test_models.py`, `test_services.py`,
`test_views.py`, `test_policy.py`, `test_crypto.py`, `test_attachments.py`,
`test_notifications.py`, `test_ws_inventory.py` — the last one asserts NO new consumer, see
`notifications/tests/test_ws_inventory.py` for the existing pattern to mirror negatively).

**Wiring points (existing files to touch, named precisely):**
- `src/django_core_micha/settings/settings_base.py:106-110` — `CORE_APPS` list; add
  `"django_core_micha.messaging"` after `"django_core_micha.notifications"`. This is the shared
  base settings inherited by every consuming app (`from django_core_micha.settings.settings_base
  import *` — see jg-ferien's `backend/settings.py:3`), so this one line is what makes the new
  tables/migrations reach every app on the next dcm pin bump — no per-app INSTALLED_APPS edit
  needed downstream.
- `src/django_core_micha/api_urls.py:8,73` — import `messaging` urls alongside the existing
  `notifications_urls` import (line 8 pattern) and `path("messaging/", include(messaging_urls))`
  next to line 73's `path("notifications/", include(notifications_urls))`. Base path per design
  §REST is `/api/messaging/` — this wiring is what produces that prefix (mounted under `/api/`,
  confirm by reading the project urls.py that includes `api_urls` under `/api/` — same pattern as
  `notifications/`).
- `pyproject.toml:7` — version bump, **once at WO end**, not per chunk (envelope: "One version bump
  + PyPI publish at WO end").
- `CHANGELOG.md` — one entry at WO end, in the exact style of the existing entries (see the
  NOTIF-19 / NOTIF-12 entries already in the file: `## [x.y.z] — date` then `### Added` with
  prose bullets explaining the *why*, not just *what*; this repo's changelog entries are detailed
  design-rationale prose, not one-liners — match that density for the messaging domain entry).

**Reference implementations to generalize (read, do not copy wholesale — jg is event-specific,
this must be tenant/app-generic per the design's `app_key` isolation requirement):**
- `../jg-ferien/backend/messaging/models.py` (249 lines) — field-level precedent for every model;
  cross-check every field named in design §Domain model against jg's actual field before assuming
  a type/constraint.
- `../jg-ferien/backend/messaging/services.py` (935 lines) — business logic precedent: read-tick
  semantics (`get_conversation_read_state`, ~line 240), DM privacy carve-out, `can_post` permission
  logic (~line 261) as the shape `MessagingPolicy.can_post` should generalize, mute/unread queries,
  reactions, polls, moderation, `_publish_message_created` (~line 606) as the shape for the new
  service's send-path (WS fan-out via `push_to_users`, then `notify()` inside its own
  `transaction.atomic()` per the exact recipe already read and confirmed accurate in the MSG-1
  review — email/push only, no chip, `feed_visible=False`, sender+muted excluded, failure caught
  and logged, never rolls back the message).
- `../jg-ferien/backend/messaging/notifications.py` (61 lines) — `register_notification_type`
  call shape; the new subpackage registers its OWN type key (design: "registration itself stays
  per-app" — re-read this envelope line, each consuming app registers its own messaging
  notification type via a call into this subpackage's registration helper, dcm does not
  self-register a type at import time for a key it doesn't own the semantics of; if this reads as
  ambiguous, treat it as: dcm exposes `register_messaging_notification_type(app_key, ...)`,
  consuming apps call it in their own `apps.py.ready()`, exactly mirroring how jg's own
  `messaging/apps.py` currently calls its local `register_messaging_notification_types()` — check
  that file too if present).
- `../jg-ferien/backend/messaging/fields.py` (101 lines) — `EncryptedTextField`/`MultiFernet`
  precedent; this WO's `crypto.py` generalizes from a single global `MESSAGE_ENCRYPTION_KEY`
  setting (jg today) to a per-app `MESSAGING_KEYRINGS[app_key]` dict (design §Encryption) — the
  `MultiFernet`/rotation/`InvalidToken`-as-legacy-plaintext logic itself carries over, the key
  *lookup* does not.
- `../jg-ferien/backend/messaging/event_chat_sync.py` (110 lines) — managed-membership signal-sync
  precedent for `MessagingPolicy.provision_membership`'s contract shape (not implemented here —
  jg's own adoption of the hook is MSG-5, out of scope — but read to confirm the hook's
  `{members, external_key, remove_absent}` return shape (design §App hook contract) is actually
  sufficient to express what jg's signals currently do).
- `src/django_core_micha/validators/upload.py` (110 lines, already read during MSG-1 review) —
  `validate_upload(file_obj, *, allowed_mimes, max_size)` and `detect_mime` (uses the `filetype`
  package). Chunk 0's pre-check: confirm/extend this for OOXML/ODF vs. bare-ZIP per the design's
  documented caveat. `filetype`'s archive matchers are in `filetype.types.archive` — check whether
  its `Zip`/docx-family matchers already exist as separate types before assuming a gap; if
  `filetype` genuinely cannot distinguish, add a `zipfile`-based member-inspection fallback
  (`word/`/`xl/`/`ppt/` prefix check for OOXML, `mimetype` entry check for ODF) — stdlib `zipfile`
  only, no new dependency per the envelope's explicit "no dependency changes without prior operator
  approval".
- `src/django_core_micha/auditlog/models.py` + `registry.py` (already read during MSG-1 review) —
  confirms `MessagingAuditEvent` is deliberately a separate model, not a `register()` call into
  this registry (per the design doc's now-documented rationale).
- `src/django_core_micha/notifications/api.py`, `types.py`, `delivery.py` — `notify()` signature,
  `NotificationType`/`register_notification_type`, `push_to_users`; confirm the `expires_at=`
  kwarg does not exist yet on `notify()` (design/envelope: "the NEW dcm API `notify(expires_at=…)`
  closes the NOTIF-21 gap") — this is new work in `notifications/api.py`, not messaging-only; adding
  it there is in scope for this WO (it is additive to `notify()`, not to messaging's own tables).
  Cross-check `notifications/models.py` for whether `Notification`/`NotificationRecipient` already
  carry an `expires_at`-shaped field before adding one — if not, this needs its own additive
  migration in the `notifications` app (not `messaging`), called out separately in chunk 4's commit
  message.
- `src/django_core_micha/notifications/management/` (check for existing `prune_notifications`
  command referenced by design/envelope for TTL) — confirm the command's current retention query
  shape before adding `expires_at`-aware pruning logic to it.

**Binding spec (already independently reviewed, do not re-derive):** `docs/design/messaging-platform.md`
in full — especially §Domain model (exact field lists), §App hook contract (exact `Protocol` method
signatures — copy them verbatim, do not paraphrase), §Encryption-at-rest (STOP clause is a hard
runtime behaviour: fail-closed on missing/malformed/empty/shared rings, not just a design note),
§REST contract (exact endpoint list + permission per endpoint), §Realtime (exact frame names),
§Notification contract (exact channel/persistent/feed_visible/dedup answers per event type),
§Attachments (allowlist + the now-added container-format caveat), §Volume/retention (the
`expires_at`/TTL requirement).

**Invariants / do-not-touch:**
- No app-specific code anywhere in this subpackage — no `Event`/`spesix`/jg imports, no
  hard-coded app_key branching. Every app-specific decision goes through `MessagingPolicy`.
- No new WS consumer. No client→server WS path anywhere (no consumer, no view that accepts a
  WS-originated write).
- No dependency changes without prior operator approval (envelope, explicit).
- No behavioural change to existing `notifications` app behaviour beyond the additive `expires_at`
  capability — existing dispatch/router/prefs/feed logic for non-expiring types must be
  byte-identical.
- Additive-only migrations — no altering/dropping any existing table (this is the `[approval
  schema]` Tier-2 gate's actual content: new tables only).
- Never weaken the encryption fail-closed behaviour to make a test pass — a test that wants
  plaintext fallback is testing the wrong thing.
- `django_core_micha.auth` core untouched (envelope, explicit) — permission enforcement for
  messaging lives entirely in the new `policy.py`/views layer.

**Known pitfalls:**
- jg's `Conversation` has no `app` field (single-tenant per deployment) — every new model needs the
  `app` FK/field design specifies; do not silently drop tenant scoping to make a jg-parity test
  pass more easily.
- The DM read-status privacy carve-out must hold **even for users with `read_receipt_detail`
  capability** — moderators still get zero per-recipient detail on DMs, only aggregate. This is the
  single easiest invariant to accidentally break while generalizing jg's `get_conversation_read_state`
  (jg has no moderator-detail concept at all today — it's new in v1, easy to over-apply broadly).
- Idempotency (`client_request_id` + `Idempotency-Key`) must produce exactly one message on retry,
  not silently succeed twice nor 409 — check the design's exact wording (§REST) again at
  implementation time.
- Cursor pagination is a **new** ucm-facing contract (jg's own pagination may not have it per MSG-1
  §8's noted gap) — do not copy a jg pagination shape that turns out to be offset-based.

### Chunk-specific invocation notes

For each chunk (0/1/2/3/4), the Codex prompt = this file's Part A + Part B, **plus** an appended
line: `"Build ONLY chunk <n> of the chunk plan above. Do not start chunk <n+1>. Leave the working
tree uncommitted for the orchestrator's review."` Chunk 0 may be skipped/folded into chunk 4 per the
envelope if the pre-check resolves trivially (`filetype` already handles OOXML/ODF correctly) — the
orchestrator decides this after reading chunk 0's result, not Codex.

### Required tests

Per envelope "Required tests to WRITE" section — scoped per chunk (each chunk writes and runs only
its own slice: chunk 1 → keyring tests, chunk 2 → policy/permission/recipient tests, chunk 3 →
REST/realtime/pagination tests, chunk 4 → attachment/TTL tests), plus the WO-end affected-area gate:
full `notifications` + `messaging` suites together (confirm zero regression in existing
notifications behaviour).

### Progress contract

Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (file opened, file
edited, command/test run) and `PROGRESS: [<n>/<total>] done` on step completion, spaced so no gap
exceeds ~2 min, stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>` **per
chunk invocation**.

### Preamble (must be appended verbatim to every chunk's Codex prompt)

The text above is the COMPLETE spec for this chunk — read nearest `AGENTS.md`,
`.codex/skills/<role>/SKILL.md` (if present), and this repo's `MEMORY.md` only for conventions;
stay in scope; do not touch anything outside `src/django_core_micha/messaging/` and the named
wiring points; do not touch auth/CI/dependencies without prior operator approval; do not update
`MEMORY.md`; do NOT `git add`/`commit`/`push` — leave the chunk's changes uncommitted in the working
tree for the orchestrator's independent review. WRITE the tests this chunk's slice requires AND RUN
them to confirm they execute and pass — that is the ONLY test run you do: do NOT run the full
`notifications`/`messaging` suite and do NOT run any review. The orchestrator runs the chunk-scoped
tests gate and the independent review after you finish.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2.md` in `django-core-micha` (main), chunk by chunk
starting at chunk 0/1. `git pull` first, read the WO + `docs/design/messaging-platform.md`, then
follow `orchestrate-codex` (Codex-first per chunk, own review per chunk, one publish at WO end).
