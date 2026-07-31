# MSG-2c — dcm messaging contract gaps: realtime frames, poll read path, conversation preview

Status: planned · Tier 2 (published contract, additive; **no schema change**) · Target repo: `django-core-micha` (main)
**Binding spec:** `docs/design/messaging-platform.md`, including the 2026-07-31 amendment
§"Poll read contract and conversation preview". On any conflict the design doc wins.

Extends the landed MSG-2 (2.36.0) and MSG-2b (2.36.1), same convention as `NOTIF-8b`/`8c`.

---

## Part A — Envelope (Expertenchat, 2026-07-31)

### Goal

Make the dcm messaging contract actually deliver what the design promises, so the client can render
what it was built to render. Three gaps found by the post-MSG-3 audit, all backend-side.

### Background — what is wrong today

1. **9 of 12 designed realtime frames are never emitted.** `services.py` has exactly three
   `transaction.on_commit(lambda: _publish(...))` call sites — lines 158, 173, 187 — emitting
   `message`, `message_edited`, `message_deleted`. `add_reaction`/`remove_reaction` (191-201),
   `vote_poll`/`close_poll` (215-236), `mark_read`/`mark_delivered`/`mark_thread_read` (252-271),
   `archive_conversation`, `set_preferences` and `reconcile_membership` publish nothing. ucm holds
   handlers for five frames that can never fire.
2. **A poll cannot be read.** No `serialize_poll` exists; `ConversationPollView` returns
   `{id, message_id, closed_at}`. The design never specified a read path either — **that design gap is
   closed as of the 2026-07-31 amendment**, which this WO implements.
3. **`serialize_conversation` has no `last_message`** (only `last_message_at`), so a conversation list
   can sort by recency but never show a preview.

None of this was catchable by dcm's own tests, which exercise services against the database and never
ask whether a client can render the result.

### Expected outcome

**Poll read path** — per design §"Poll read contract and conversation preview":
- `serialize_poll(poll)` producing the **viewer-independent core**: `{id, question, allow_multiple,
  closed_at, created_by_id, options: [{id, text, order, vote_count, voters}]}`, with `question`/`text`
  decrypted under the app ring. `voters` carries participant **user ids**, never names, in **every**
  conversation kind including `direct`.
- **`voted_option_ids` is added only by REST call sites**, which know the requesting user. It must not
  be part of `serialize_poll`'s return value.
- Embedded by `serialize_message` when `kind == "poll"`, key omitted otherwise — **core projection
  only**. This is the critical constraint: `serialize_message` is embedded verbatim into the already
  shipped `message`/`message_edited` frames (`realtime.py`), computed once and fanned out identically to
  every recipient. Embedding a viewer-specific field there would leak one viewer's vote to all of them
  and break the viewer-independence rule. Implement the split first; do not "simplify" it away.
- Returned by `POST conversations/{id}/polls/`, `POST polls/{id}/vote/` and `POST polls/{id}/close/`
  (core **plus** `voted_option_ids`), so no mutation needs a follow-up read. **No standalone
  `GET polls/{id}/`.**

**Realtime frames** — emit the missing ones, on commit, only to policy-resolved live recipients,
carrying `envelope`/`event_id` per design §Realtime:
- `reaction` (add + remove), `poll_updated` (vote + close), `read_state` (`mark_read`),
  `thread_read_state` (`mark_thread_read`), `delivered` (`mark_delivered`),
  `conversation_upsert` (create/open + last-message change), `conversation_archived`,
  `participant_changed` (membership reconcile).
- **`attachment_ready` is deliberately NOT emitted** — v1 has no scanner and the attachment pipeline
  validates, re-encodes and persists synchronously, so there is no asynchronous "ready" moment to
  signal. Already recorded in the design doc's §Realtime as reserved-and-unemitted; do not implement it,
  and do not treat its absence as an oversight to fix.
- **Viewer-independent payloads only** (design amendment): no frame may carry a per-viewer field.
  `conversation_archived` and the preference-derived frames are participant-local by nature — fan them
  out to that participant only, not to the conversation.

**Conversation preview**: `serialize_conversation` gains `last_message` =
`{id, sender_id, kind, excerpt, created_at}` or `null`. `excerpt` is the decrypted body truncated to a
server-owned bound, empty for a soft-deleted message, and the poll question for a poll message.

**Release**: one patch/minor bump + PyPI publish at WO end, with a `CHANGELOG.md` entry in the
established prose style.

### Non-goals / do-not-touch

Any schema change — this WO is serialization and fan-out only; if a migration appears necessary, stop
and return to the operator. No new WS consumer and no client→server WS path (design §Realtime;
`test_ws_inventory` must keep asserting this). No scanner infrastructure. No ucm work (`MSG-3b`). No
change to existing notification behaviour. No app-specific code. No dependency changes. Do not alter
the `message`/`message_edited`/`message_deleted` frames that already work.

### Required tests to WRITE

- **Poll projection:** question and option text decrypt correctly; `vote_count` matches votes; `voters`
  is present in every conversation kind including `direct`; `voted_option_ids` is per-viewer (two
  viewers of the same poll get different values) and is returned by REST only.
- **Viewer-independence (the load-bearing test):** `voted_option_ids` is absent from **every** realtime
  frame — assert it for `poll_updated` **and** for `message`/`message_edited` carrying a poll message.
  A frame captured for one recipient must be byte-identical to the same frame captured for another.
- **Poll embedding:** a `kind == "poll"` message carries `poll`; every other kind does not; create,
  vote and close all return the same projection shape.
- **Each newly emitted frame:** fires exactly once, after commit, only to policy-resolved live
  recipients, carries `envelope: "messaging"` and an `event_id`, and **carries no viewer-specific
  field** (assert `voted_option_ids` is absent from `poll_updated`).
- **Participant-local frames:** `conversation_archived` reaches only the participant who archived, not
  the conversation.
- **`last_message`:** excerpt is bounded; empty for a soft-deleted message; the question for a poll;
  `null` when the conversation has no messages; decrypts under the correct app ring.
- **No regression:** the existing three frames are byte-compatible; `test_ws_inventory` still asserts no
  consumer was added; full notifications + messaging suites green at WO end.

### Risks

- **Frame volume.** `read_state` and `delivered` can fire on every read of every conversation. Emit only
  on a meaningful watermark advance, never on a no-op re-mark, and say so in the implementation. Getting
  this wrong turns a chat into a fan-out storm.
- **Decryption cost on list pages.** `last_message` decrypts one message per conversation per page —
  bounded page size keeps it acceptable, but do not extend the preview to more than the excerpt.
- **Voter privacy.** The `direct` omission is the load-bearing rule and the easiest to lose while
  generalizing; it has a mandatory test.
- **Scope creep into the frames nobody needs.** `attachment_ready` is explicitly out; do not add frames
  beyond the design's list.

### Preconditions

MSG-2 + MSG-2b published (2.36.1, met). Design amendment §"Poll read contract and conversation preview"
committed (met, same session). Approval Gate #1 = operator go on this envelope.
**This WO unblocks rows 38, 51-53 and 56-58 of `ui-core-micha` `MSG-3b`** and should ship before them.

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2c.md` in `django-core-micha` (main). `git pull` first, read the
WO + `docs/design/messaging-platform.md` (§"Poll read contract and conversation preview", §Realtime),
then follow `orchestrate-codex` (Codex-first, own independent review, commit on green, one publish at
WO end).

---

## Part B — Implementation map (Orchestrator)

To be filled by the Orchestrator session on `git pull`, within the envelope above.
