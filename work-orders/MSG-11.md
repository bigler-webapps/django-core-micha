# WORK ORDER MSG-11 (django-core-micha) — `vote_poll` never retracts a multi-select vote

Found by the Orchestrator while implementing `ui-core-micha` MSG-6g (tap-to-vote poll UI):
that WO's scope A assumes `option_ids` is the caller's complete, authoritative vote set per
call — a toggle-off just resends a smaller set. That assumption does not hold.

## TIER
Tier 2 — shared-core, consumed by every app. Bundled into the same patch release as
`MSG-10`'s post-review fixes (R1/R2) rather than a separate publish, per operator
confirmation in chat ("können wir das in dcm direkt fixen?").

## DEFECT

`vote_poll` (`services.py:247-259`, before this fix):

```python
with transaction.atomic():
    if not poll.allow_multiple:
        PollVote.objects.filter(option__poll=poll, user=actor).delete()
    PollVote.objects.bulk_create([PollVote(option=option, user=actor) for option in options], ignore_conflicts=True)
```

Only the single-choice branch clears prior votes before creating new ones. Multi-select
never deletes — it only ever `bulk_create`s. So voting `[A, B]` and later re-voting `[A]`
does **not** retract the vote for `B`; the row persists forever. This is a **pre-existing**
gap, not introduced by any recent WO — today's two-step "select checkboxes, then click
Vote" flow sends the same `option_ids` set through the same endpoint and has the identical
bug, just harder to notice without instant per-tap feedback.

**Confirmed via code reading** (`services.py`, `views.py:356-361` `PollVoteView`) — no
diffing/deletion logic exists anywhere between the view and the service. No existing test
covered multi-select retraction at all.

## FIX

`option_ids` is now authoritative for both single- and multi-select: delete any of the
actor's existing votes for options *not* in the new set, then (re-)create the requested
set with `ignore_conflicts=True` (safe against the `(option, user)` unique constraint —
re-voting an already-selected option is a no-op, not a duplicate-row error). This also
simplifies the function: no more single/multi special case.

## TESTS ADDED
`tests/test_services.py`:
- Multi-select: voting `[A,B]` then `[A]` leaves only `A`.
- Multi-select: voting `[A,B]` then `[]` retracts everything.
- Multi-select: re-voting an already-selected option does not duplicate.
- Single-choice regression guard: switching `A` → `B` still clears `A` (unaffected by the
  fix).

## SCOPE
Backend only, `vote_poll` in `services.py`. Does not touch `create_poll`, `close_poll`,
poll serialization, or the WS `poll_updated` payload shape.

## STATUS
Implemented directly by the Orchestrator (Codex unavailable — quota exhausted on the same
session's `MSG-10` attempt). Bundled into `MSG-10`'s independent-review pass and patch
release; see `WORK_ORDERS.md` for both rows.
