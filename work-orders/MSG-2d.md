# MSG-2d — readable thread reply state + managed-conversation identity

Status: planned · Tier 2 (published contract, additive; **no schema change**) · Target repo: `django-core-micha` (main)
**Binding spec:** `docs/design/messaging-platform.md` §"Thread reply state and managed-conversation
identity" (amendment, 2026-07-31). On any conflict the design doc wins.

Extends MSG-2 / MSG-2b / MSG-2c (2.37.0), same convention as `NOTIF-8b`/`8c`.

---

## Part A — Envelope (Expertenchat, 2026-07-31)

### Goal

Close the last two read-side contract gaps found by MSG-3b, so `ui-core-micha` rows 27 and 42 stop being
BLOCKED. Both are additive serialization; neither touches the schema.

### The findings

Both were found by MSG-3b attempting to render against the real contract and correctly refusing to
fabricate the data client-side.

1. **Thread reply state is write-only.** `mark_thread_read` writes a `MessageThreadReceipt`; nothing
   ever reads one back. `serialize_message` has no `reply_count`. A freshly-mounted client cannot know
   that a root message has replies, nor whether they are unread, without expanding every thread by hand.
   MSG-2c's `thread_read_state` frame reports a *change* and gives a new connection no starting point.
2. **`external_key` is not serialized.** It carries the app's own identity for managed and broadcast
   conversations (jg: `event_all` vs `event_team`), so without it a client sees `kind: "managed"` twice
   and cannot tell the two apart or label them.

### Expected outcome

Per the design amendment:

- `serialize_message` gains **viewer-independent** `reply_count` and `last_reply_at` (`null` when the
  message has no replies). Because `serialize_message` is embedded verbatim into the `message` and
  `message_edited` frames, these MUST be viewer-independent — the same constraint that governs the poll
  embed.
- **`thread_last_read_at` is viewer-specific and REST-only**: added by the same call sites that add
  `voted_option_ids`, computed from the requesting user's own `MessageThreadReceipt`, `null` when none
  exists. It must never appear on any realtime frame.
- `serialize_conversation` gains `external_key`, `null` where the kind does not use one.
- Counting must not become an N+1 across a message page — annotate/aggregate, do not query per message.

### Non-goals / do-not-touch

Any schema change (if a migration appears necessary, stop and return to the operator). No new frame
types — MSG-2c's set is complete and `attachment_ready` stays reserved-and-unemitted. No change to the
existing frames' payloads beyond the two additive viewer-independent message fields. No ucm work
(`MSG-3c`). No scanner, no dependency changes, no client→server WS, no new WS consumer.

### Required tests to WRITE

- **`reply_count` / `last_reply_at`:** correct for a root with no replies (`0` / `null`), with several
  replies, and after a reply is soft-deleted — **a soft-deleted reply still counts** (pinned in the
  design amendment: the thread renders it as a tombstone, so the count must match what is displayed).
- **`thread_last_read_at` is per-viewer:** two viewers of the same root get different values; a viewer
  with no receipt gets `null`.
- **Viewer-independence (load-bearing):** `thread_last_read_at` is absent from **every** realtime frame
  — assert it for `message` and `message_edited` specifically, alongside the existing
  `voted_option_ids` assertion. A frame captured for one recipient stays byte-identical to the same
  frame captured for another.
- **`external_key`:** present for managed and broadcast, `null` for direct/group/object_thread.
- **No N+1:** rendering a page of messages issues a bounded number of queries regardless of page size
  (assert with `assertNumQueries` or equivalent).
- **No regression:** full `pytest -q` green — note this now genuinely includes the messaging suite
  (`DX-2`).

### Risks

- **N+1 on reply counts** is the realistic failure here; it has a mandatory test.
- **Leaking the viewer receipt into a frame** — the exact mistake the poll amendment was written to
  prevent, now with a second field that can make it. Same mandatory assertion.
- Deleted-reply counting is a genuine semantic choice, not an implementation detail; pin it rather than
  discovering it later from a UI that disagrees with the count.

### Preconditions

MSG-2c published (2.37.0, met). Design amendment §"Thread reply state and managed-conversation identity"
committed (met, same session). Approval Gate #1 = operator go on this envelope.
**Unblocks rows 27 and 42 of `ui-core-micha` MSG-3b's checklist**, to be delivered by a later ucm WO.

### Release

One patch/minor bump + PyPI publish at WO end, with a `CHANGELOG.md` entry in the established style.

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2d.md` in `django-core-micha` (main). `git pull` first, read the
WO + `docs/design/messaging-platform.md` §"Thread reply state and managed-conversation identity", then
follow `orchestrate-codex` (Codex-first, own independent review, commit on green, one publish at WO end).

---

## Part B — Implementation map (Orchestrator)

To be filled by the Orchestrator session on `git pull`, within the envelope above.
