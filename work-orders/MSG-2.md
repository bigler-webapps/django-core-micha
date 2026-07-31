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

0. **Pre-check:** verify OOXML/ODF vs. bare-ZIP detection (design §Attachments container-format caveat);
   if needed, extend `validators.upload` with caller-supplied allowlist config + a content-aware container
   check (stdlib `zipfile` member inspection preferred — **no new dependency without prior operator
   approval**). May fold into chunk 4 if trivially resolved.
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

_To be filled by the Orchestrator on `git pull`, within this envelope: context package, progress
contract, execution directive, mini-handover. The envelope above and the design doc are authoritative;
scope changes go back to the operator._
