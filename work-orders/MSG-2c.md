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

### Execution directive (place first when generating the Codex prompt)

> Implement through `codex exec` in the background — invoked directly via Bash (never the
> `debugger`/`*_coder` Agent wrappers) with BOTH flags `--skip-git-repo-check` and
> `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file.
> Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

### Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root — package lives at
`src/django_core_micha/messaging/`). Never `…\webapps`.

### Context package

**Named files to change** (all under `src/django_core_micha/messaging/`):

1. **`serializers.py`**
   - Add `serialize_poll(poll)` — viewer-independent core only, per design amendment:
     `{id, question, allow_multiple, closed_at, created_by_id, options: [{id, text, order,
     vote_count, voters}]}`. `question`/`option.text` decrypt via `decrypt_text(app_key=..., value=...)`
     exactly like `serialize_message`. `voters` = list of participant user ids who voted that option
     (query `PollVote` per option, or one query across `poll.options` and group in Python — either is
     fine, this is a bounded list per message). **Never** include `voted_option_ids` here — that is
     REST-only and added by the view after calling this.
   - `serialize_message(message)`: when `message.kind == "poll"`, add a `"poll"` key =
     `serialize_poll(message.poll)`; omit the key for every other kind (do not set it to `None`).
     Guard the reverse one-to-one access (`message.poll` raises `Poll.DoesNotExist` if absent — only
     relevant defensively; a `kind == "poll"` message always has a poll in practice, but callers should
     not crash on a lookup failure — use `getattr` / `hasattr` or an explicit try/except, whichever
     reads cleanest here). Call sites already `prefetch_related("attachments", "reactions")`
     (`views.py`, `realtime.py`) — extend those to also prefetch the poll so this stays N+1-free:
     `prefetch_related("attachments", "reactions", "poll__options__votes")` (the poll's
     `options`/`votes` are what `serialize_poll` walks).
   - `serialize_conversation(conversation, participant)`: add `last_message` = `{id, sender_id, kind,
     excerpt, created_at}` or `null`. Compute from the conversation's newest message (there is no
     existing `last_message` FK — query `conversation.messages.order_by("-created_at").first()`, or
     have the call site pass it in / annotate; either is fine as long as it stays one query per
     conversation on the list page, matching the design's "decrypts one message per conversation"
     budget). `excerpt`: decrypted `body` truncated to a server-owned bound (pick a constant, e.g.
     140 chars — document it as a module constant, not a magic number); empty string for a
     soft-deleted message (`deleted_at is not None` → body is already cleared to `None` by
     `soft_delete_message`, so excerpt is `""`); for `kind == "poll"`, excerpt is the poll's decrypted
     `question`, not the (empty) message body.

2. **`services.py`** — add realtime fan-out. Follow the existing `transaction.on_commit(lambda: _publish(...))`
   pattern at lines 158/173/187 exactly (recipients re-resolved live inside the callback, not
   captured before commit):
   - `add_reaction`/`remove_reaction` (191–201): wrap body in `with transaction.atomic():`, publish
     `"reaction"` on commit to `resolve_live_recipients(conversation=message.conversation)` — aggregate
     payload only (e.g. `{"message_id": str(message.id), "reactions": serialize_reactions(message)}`,
     reusing the existing aggregate helper from `serializers.py`); never per-user emoji ownership.
   - `vote_poll`/`close_poll` (215–236): publish `"poll_updated"` on commit to
     `resolve_live_recipients(conversation=poll.message.conversation)`, payload
     `{"message_id": str(poll.message_id), "poll_id": str(poll.id), "poll": serialize_poll(poll)}` —
     **never** `voted_option_ids`. `vote_poll` currently returns `None`; change it to `return poll` (or
     the refreshed poll) so the view can build the REST response without a second query. Both already
     run inside function bodies without an outer `transaction.atomic()` for `vote_poll` (it has one
     internally already) — `close_poll` currently has none; add one so `on_commit` is well-defined.
   - `mark_read`/`mark_delivered`/`mark_thread_read` (252–271): **only** publish when the watermark
     actually advances (the existing `if ... is None or timestamp > ...:` branch is already the advance
     guard — track whether that branch ran and only schedule the frame inside it). Frame types
     `"read_state"`, `"delivered"`, `"thread_read_state"` respectively, fanned to
     `resolve_live_recipients(conversation=...)` (thread-read uses `root.conversation`). Keep payloads
     aggregate/viewer-independent — the identity of *who* advanced their own watermark is not
     per-viewer (it's the same fact for every recipient), but never include one recipient's own
     `last_read_at` next to another's. A `{"user_id": str(actor.pk)}` shape (plus the new watermark
     value) is sufficient; do not add `recipient_detail`-style per-participant breakdowns here — that
     stays REST-only (`read_status`, already gated on `read_receipt_detail` and never for `direct`).
   - `archive_conversation` (283–287): publish `"conversation_archived"` on commit, fanned **only to
     `actor`** (`[actor]`, not `resolve_live_recipients`) — participant-local per the design amendment.
     Minimal payload, e.g. `{"archived": bool(archived)}`.
   - `reconcile_membership` (47–75): publish `"participant_changed"` on commit to the conversation's
     current live participants (query fresh after commit, same pattern as recipients elsewhere), once
     per call — not once per changed row.
   - `send_message` (127–160) and `open_direct`/`create_conversation` (78–116): publish
     `"conversation_upsert"` on commit.
     - On `send_message`: same recipient set as the existing `"message"` frame
       (`resolve_live_recipients(conversation=conversation, sender=actor)`) — this is the
       "last-message change" trigger. Reuse the already-updated `conversation` (its `last_message_at`
       is already saved by this point in the function).
     - On `open_direct`/`create_conversation`: fan to the conversation's participants at creation time
       (open/create trigger).
     - **Payload must be viewer-independent** — do **not** reuse `serialize_conversation(conversation,
       participant)` verbatim, since it embeds `archived_at`/`muted`/`email_enabled`/`push_enabled`,
       which are per-participant and would leak one recipient's mute/archive state to every other
       recipient if fanned out as-is. Build a separate, smaller payload for the frame: `{id, app_key,
       scope_id, kind, title, last_message_at, last_message, created_at}` (the non-participant-scoped
       subset of `serialize_conversation`, reusing its `last_message` computation from `serializers.py`
       so the shapes don't drift). This is the one place in this WO most likely to introduce a privacy
       leak if implemented by literal reuse — do not shortcut it.
   - `set_preferences`: **no frame** — not in the design's frame vocabulary (§Realtime) and not in the
     WO's "Realtime frames" list; leave it silent, as today.

3. **`realtime.py`** (`publish_messaging_event`): currently special-cases `message`/`message_edited`
   to embed `serialize_message`. No change needed there for the new frame types **if** services.py
   builds each frame's full payload before calling `_publish`/`publish_messaging_event` (i.e. pass the
   already-serialized `poll`/`reactions` dict in `payload`, the same way `message_deleted` already
   passes opaque fields without a lookup). Prefer this over adding more `event_type`-branching inside
   `publish_messaging_event` — keep the "views/services chunk supplies safe serializers" comment at the
   top of this file true. If a lookup-after-commit *is* needed for a frame (mirroring how `message`
   does it), keep it minimal and viewer-independent, same discipline as the existing branch.

4. **`views.py`**:
   - `ConversationPollView.post` (263–269): response body currently `{id, message_id, closed_at}`.
     Change to `serialize_poll(poll)` plus `voted_option_ids` (computed for `request.user` — a freshly
     created poll has none, so this is normally `[]`, but compute it properly rather than hardcoding).
   - `PollVoteView.post` (272–277): currently returns `204 No Content` with no body. Change to `200 OK`
     with `serialize_poll(poll)` plus `voted_option_ids` for `request.user` (query `PollVote` for this
     poll/user after the service call — `vote_poll` now returns the poll, see above). This is a
     response-shape change on an existing endpoint; the design amendment explicitly calls for it
     ("`POST polls/{id}/vote/` ... return[s] the same projection, so no mutation needs a follow-up
     read") — implement it, it is in scope, not a regression.
   - `PollCloseView.post` (280–285): currently returns `{id, closed_at}`. Change to `serialize_poll(poll)`
     plus `voted_option_ids` for `request.user`, same shape as the other two.
   - No new endpoint — **no `GET polls/{id}/`** (explicit non-goal, design amendment is explicit this
     is not needed).

### Invariants / do-not-touch / pitfalls

- **Viewer-independence is the load-bearing constraint of this whole WO.** Every frame payload must be
  byte-identical regardless of which recipient receives it. `voted_option_ids` must never appear in
  `serialize_poll`'s return value, `poll_updated`, or in the `poll` key embedded in `message`/
  `message_edited` (which already flow through `serialize_message` unchanged from MSG-2 — do not touch
  those two frames' existing fields, only the new embedded `poll` key inherits this rule).
- **`voters` is present in every conversation kind including `direct`** — no DM carve-out (design
  amendment is explicit and corrects an earlier wrong draft; do not reintroduce the carve-out).
- **No schema change.** If any of the above seems to need a new field/migration, stop and return to
  the operator — do not add one.
- **No new WS consumer, no client→server WS path.** `test_ws_inventory.py` must keep passing unchanged
  in its assertion shape (`assert_all_consumers_secure([...]) == []` and no `consumers` module) — all
  new frames go through the existing `publish_messaging_event`/`push_to_users` Layer-1 path.
- **`attachment_ready` stays unemitted.** Do not add it; it is reserved, not a gap.
- **Frame volume guard:** `read_state`/`delivered` must fire only on an actual watermark advance, never
  on a no-op re-mark (e.g. calling `mark_read` twice with the same or earlier timestamp) — this is
  explicitly called out as a risk in Part A and has a required test.
- **`last_message` decryption cost:** one decrypt per conversation per list page, bounded excerpt length
  — do not extend the preview beyond the excerpt (e.g. no full body, no attachment list).
- Keep the existing three working frames (`message`, `message_edited`, `message_deleted`) byte-compatible
  — the only permitted change to `message`/`message_edited` is the new embedded `poll` key when
  `kind == "poll"`.

### Required tests to WRITE (Codex writes them; the ORCHESTRATOR runs them)

Per Part A "Required tests to WRITE" — write in `tests/test_services.py` (realtime fan-out, watermark
guards, poll mutation return shape), `tests/test_serializers.py` (create if it does not exist — poll
projection, viewer-independence of `serialize_poll`, `last_message` excerpt rules) and
`tests/test_views.py` (poll endpoint response shapes, `voted_option_ids` per-viewer via REST) —
whichever module already covers the neighbouring behaviour; do not invent a new test module layout if
an existing one fits. Extend `tests/test_ws_inventory.py` only if a new frame needs an inventory-style
assertion analogous to the existing ones — do not restructure that file.

### Release (do last, after tests are written and passing)

- Version bump in `pyproject.toml`: **minor** (additive contract change, matches the 2.36.0/MSG-2
  precedent for "Added" changes vs. 2.36.1/MSG-2b's patch for a pure fix) → `2.37.0`.
- `CHANGELOG.md` entry in the established prose style (see `[2.36.1]`/`[2.36.0]` entries) under
  `## [2.37.0] — 2026-07-31`, `### Added`, titled `MSG-2c — poll read contract, conversation preview,
  realtime frame completion`. Summarize: the poll read split (core vs. `voted_option_ids`), the
  `last_message` preview field, and the newly emitted frames — in the same density as the existing
  entries, not a line-by-line diff dump.

### Preamble (append verbatim to the Codex prompt)

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`,
> and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests the
> `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute and
> pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish — those are
> the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.
