# MSG-2f — group/broadcast conversation lifecycle: membership reconciliation + idempotency

Status: planned · Tier 2 · Target repo: `django-core-micha` (main)

---

## Part A — Envelope (Expertenchat, 2026-08-01)

### Goal

`create_conversation()` (`src/django_core_micha/messaging/services.py:99-121`) must give `group` and
`broadcast` conversations the same two guarantees `managed`/`object_thread` already get: live
provider-resolved membership (not a one-time creation-time snapshot) and safe re-open (no duplicate
rows, no crash on a second launch of the "same" conversation).

### Origin

Found 2026-08-01 by `jg-ferien` MSG-5d ("cut over to the shared messaging platform") while wiring its
frontend launchers against the real REST contract. jg's own conversation kinds (per-event broadcast,
per-Group group chat) need `group`/`broadcast` to behave like stable, reusable conversations with live
membership — exactly what `managed` already provides for `event_all`/`event_team` — but the current
`create_conversation` code path does not give them that. jg's Orchestrator (Codex, instructed to
STOP-and-surface rather than patch dcm from a jg session) confirmed the gap by reading the code, not by
reproducing a live failure — this WO's own diagnosis phase should reproduce it directly against dcm's
own test suite before designing the fix, per the MSG-2c/2d precedent.

### Confirmed facts (read directly, cite line numbers when diagnosing further)

- `create_conversation` (`services.py:99-121`) always does `Conversation.objects.create(...)`
  (`:113`), never `get_or_create`, against a schema that already has a
  `UniqueConstraint(condition=Q(kind__in=["managed","broadcast"]), fields=["app","scope","kind",
  "external_key"], name="msg_managed_bcast_key_uniq")` (`models.py:103`). **This affects `managed`
  too, not only `broadcast`** — a second `create_conversation` call for the same
  `(app,scope,kind,external_key)` raises `IntegrityError` regardless of kind. Diagnose during this
  WO whether jg's (and any other consumer's) `managed` conversations are in practice created through
  this exact function twice, or whether they reach `managed` conversations via a different path that
  happens to avoid the collision today — do not assume either answer, verify it.
- Membership reconciliation (`reconcile_membership`, `services.py:47-76`, itself already generic —
  calls `policy.provision_membership(conversation=conversation, trigger=trigger)` for any kind) is
  only invoked from `create_conversation` for `kind in {MANAGED, OBJECT_THREAD}` (`:118`). `group` and
  `broadcast` conversations get a **one-time snapshot** at creation (the actor plus whatever
  `participant_users` the request happened to pass, `:114-117`) and never again — membership drifts
  from reality immediately for any consumer whose group/broadcast audience changes over time (jg's
  `Group` membership does).
- `views.py:158`'s conversation-list query only returns rows where the viewer already has a
  `ConversationParticipant` row — so a group/broadcast conversation whose real audience never got
  synced past the creation-time snapshot is invisible to everyone not present at creation.
- `group` has an additional structural gap: `policy.validate_scope` can require an **object scope**
  for `group` (this is app-owned validation, e.g. jg's `platform_policy.py:234-238` requires a
  group-backed object scope) — but nothing in dcm provisions or documents a canonical way for an app
  to get a stable, reusable object scope per its own "group-like" entity. Confirm whether this is
  already solved generically by `MessagingScope.Kind.OBJECT` (any app can already
  `get_or_create(kind=OBJECT, content_type=..., object_id=...)` for any of its own models — no dcm
  change needed there) or whether something dcm-side is actually missing; document the answer in this
  WO's implementation, since jg's own separate follow-up (provisioning per-`Group` object scopes) is
  blocked on knowing this.

### Scope

1. **Idempotent creation.** `create_conversation` (or the `ConversationCreateView` call site) must not
   raise on a second call for the same `(app,scope,kind,external_key)` where that combination is
   meant to be stable and reusable — `get_or_create` against the existing unique constraint, returning
   the existing row rather than erroring, for `managed` and `broadcast` at minimum. Decide and
   document whether `group` conversations should be identity-scoped the same way (one conversation per
   object scope, no `external_key` needed — mirroring how `object_thread` is already implicitly 1:1
   with its object scope) or whether `group` deliberately supports multiple ad-hoc conversations per
   scope (in which case idempotency is not the fix `group` needs — provisioning is). This decision may
   need a design-doc amendment to `docs/design/messaging-platform.md` before implementation, per the
   MSG-2c/2d precedent (amend the design first when the existing spec doesn't answer the question).
2. **Live membership reconciliation for `group` (and `broadcast`, if audience can change — e.g. jg's
   broadcast recipients are "everyone with an active EventMembership", which changes over time just
   like group membership).** Extend `reconcile_membership`'s call sites so provider-resolved
   membership applies to these kinds too, not only `managed`/`object_thread` — either by widening the
   `kind in {...}` check in `create_conversation`, or by establishing a documented reconciliation
   trigger apps are expected to call on their own membership-change signals (mirroring how jg's
   existing `event_chat_sync.py` triggers reconciliation for `managed` today) — pick whichever matches
   the design doc's existing `provision_membership` trigger vocabulary (`scope_created`,
   `domain_changed`, `reconcile`) most cleanly; do not invent a new trigger without checking that
   vocabulary first.
3. Whatever schema change (if any) sub-step 1's decision requires.

### Explicitly NOT in scope

Any jg-side change (provisioning per-`Group` object scopes, extending jg's `MessagingPolicy.
provision_membership` to compute `group` membership snapshots, wiring `event_chat_sync.py`-equivalent
signals for jg's own `Group` model) — that is separate follow-up work in `jg-ferien`, gated on this
WO's answer to the object-scope question above, tracked back there once this WO documents its
decision. Any change to `direct`/`managed`/`object_thread`'s *existing*, already-correct behavior
beyond adding idempotency to `managed`'s creation path. `ucm` frontend changes — this is backend-only.

### Required tests to WRITE

- **Idempotency**: two `create_conversation` calls with the same `(app,scope,kind,external_key)`
  return the same row, not a second row or an `IntegrityError` — for every kind this WO makes
  idempotent.
- **Membership reconciliation for `group`/`broadcast`**: a policy-reported membership change (a user
  added/removed from the provider's `provision_membership` result) is reflected in
  `ConversationParticipant` rows after a reconciliation trigger — table-driven against the same
  provider-snapshot contract `managed` already has tests for.
- **Visibility**: a user present in the provider's live membership but absent from the original
  creation-time `participant_users` list can see the conversation via the list endpoint after
  reconciliation — this is the concrete regression `views.py:158`'s participant-gated list makes
  possible today.
- **Non-regression**: existing `managed`/`object_thread` reconciliation and creation tests stay green
  unchanged.

### Risks

- **Backward compatibility for any consumer already calling `create_conversation` for `group`
  expecting a fresh conversation per call** (if the idempotency decision changes that contract) — dcm
  currently has one real consumer (jg, not yet live) and one deferred one (spesix, MSG-4, not started)
  per the platform's own register, so the blast radius is assessable, not hypothetical-infinite; check
  both before assuming no one depends on the current behavior.
- **Silent membership loss.** Reconciliation runs `remove_absent`-gated `removed_at` writes
  (`services.py:73-74`) — extending this to `group`/`broadcast` must not retroactively remove
  legitimate participants added through a path `provision_membership` doesn't know about (e.g. a
  manually-invited member outside the policy's own membership model, if that's a pattern any consumer
  relies on). Confirm `managed`'s existing behavior here and match it, don't diverge.
- **Schema risk** if sub-step 1 needs a new constraint — additive, low risk, but still a migration on
  a shared platform table other apps' data lives in.

### Preconditions

None external — self-contained dcm work. Read `jg-ferien/work-orders/MSG-5d.md` (Part B, "Precondition
gap 3") for the full trail of how this was found, including the exact code paths already identified.

### Cross-repo rule

This WO lives in `django-core-micha` itself, so the "do not modify dcm" rule that gated the discovery
session does not apply here — this is that fix. Do not fold in unrelated `ucm` or `jg-ferien` changes.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2f.md` in `django-core-micha` (main). `git pull` first, read
the WO + `docs/design/messaging-platform.md`, then follow `orchestrate-codex`. Full dcm suite is the
test gate per this repo's own convention for shared-domain service changes (not a narrow slice) —
confirm against this repo's own AGENTS.md/test-scope convention before running.

---

## Part B — Implementation map (Orchestrator)

To be filled by the Orchestrator session on `git pull`, within the envelope above.
