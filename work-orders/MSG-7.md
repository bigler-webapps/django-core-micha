# WORK ORDER MSG-7 (django-core-micha) — serialize a `sender` object, and resolve the dead `delivered_count`

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Origin: jg-ferien `work-orders/MSG-9.md` findings 2, 5, 6, 8
and 11 — read that file's "Live verification" and "The single root cause" sections first; the evidence
is there and must not be re-derived.

## TIER
Tier 2 — shared-core API change consumed by every app on the platform. Independent `reviewer`
mandatory; `sec_reviewer` mandatory for scope A (it widens what a message payload exposes).

## THE DEFECT

`serialize_message` (`messaging/serializers.py:85`, and the compact variant at `:118`) emits `sender_id`
and **no `sender` object**. Every ucm consumer derives message ownership from `message.sender?.id`:

- `MessageBubble.jsx:38` — `isOwn = Boolean(currentUser?.id) && message.sender?.id === currentUser.id`
- `Thread.jsx` `canShowReadTicks` — `message.sender?.id === user.id && …`
- `MessageBubble.jsx:11` `senderName()` — `message.sender?.display_name`

So `isOwn` is structurally `false` for every server-loaded message. Verified end-to-end in a running app
(jg local, 2026-08-03): a message with `sender_id: 3` viewed by user `id: 3` renders
`data-message-side="incoming"`; the same message sent moments earlier in the same session renders
`"own"`, because ucm's optimistic echo injects a client-side `sender` that `reconcileMessage`'s merge
preserves until reload.

User-visible consequences, all one bug: own messages render as incoming after reload · read receipts
never appear on reloaded messages · in group and broadcast conversations every message is attributed to
"Unbekannte Person" · the reply composer reads "Antwort an Unbekannte Person" · a non-moderator loses
the edit/delete menu on their own reloaded messages.

## THE DECISION THIS WO MAKES — and why

Two coherent contracts exist. **This WO chooses: dcm serializes a `sender` object.**

Rejected alternative — ucm switches to reading `sender_id`: it is a smaller diff but it cannot fix the
"Unbekannte Person" half, because a bare id carries no display name. ucm would then need a separate
name-resolution channel, which is exactly the ad-hoc patching (`other_user_id` + jg's
`resolveDirectUserName`) this WO exists to stop repeating. ucm's three call sites were all written
against a `sender` object; dcm's serializer is the outlier.

**Consequence: ucm needs no change for findings 2, 5 and 11 — this WO alone fixes them.** If the
implementation appears to require a ucm change for those three, that is a signal the shape is wrong;
stop and report.

## SCOPE

**A. Add a `sender` object to `serialize_message`.** Shape exactly:
`{"id": <int>, "display_name": <str>, "username": <str>}` — `id` must match the existing `sender_id`
type so `message.sender.id === currentUser.id` compares correctly against what
`/api/users/current/` returns (verify the type; a string-vs-int mismatch would silently reproduce the
bug). **Keep `sender_id`** — it is part of the published contract and other consumers may read it. This
is additive.

Apply to **both** serializers (`:85` and `:118`). A shape that differs between the two is the same
defect one level down.

Derive `display_name` from the same helper the rest of dcm already uses for a user's presentational
name — do not invent a new fallback chain. If none exists, state which you used and why.

**B. Guard the N+1.** `serialize_message` runs per row in list endpoints. Every queryset feeding it must
`select_related("sender")`. `ThreadView` (`views.py:339`) and `publish_messaging_event`
(`realtime.py`) already do; **audit the conversation-messages list endpoint and any other caller** and
add it where missing. A query-count regression test is required (scope D).

**C. Resolve `delivered_count` — it is dead (finding 6).** `read_status` (`services.py:346`) computes
`delivered_count` from `ConversationParticipant.last_delivered_at`, but the only writer,
`mark_delivered` (`services.py:297`), has **zero production callers** — grep finds it only in dcm's own
tests. No endpoint, view, WS handler or signal reaches it. The field is permanently NULL, so
`delivered_count` is always `0` and ucm renders the label "Zugestellt an 0".

**Recommended resolution: remove the dead middle state rather than build delivery tracking.** Drop
`delivered_count` from `read_status`'s response and delete `mark_delivered`, leaving an honest two-state
receipt (`all_read` true/false). Rationale: real delivery tracking needs a client-side acknowledgement
on receipt, which is a feature, and it would be meaningless while live push itself is unresolved
(jg MSG-9 finding 1). **This is a product-visible decision — get the operator's explicit confirmation
before implementing C.** If they instead want delivery tracking, that is its own WO, not this one.

Deleting a public response field is an API break: state it in the changelog and bump accordingly.

## NON-GOALS / DO NOT TOUCH
- Do **not** change `serialize_conversation` / `serialize_conversation_core`. The `other_user` gap is
  known and separately handled by `other_user_id` + host-side resolution (MSG-6b); folding it in here
  widens the blast radius for no gain.
- Do **not** add or change realtime frame types. ucm discarding `read_state`/`delivered`/
  `thread_read_state` is real (jg MSG-9 finding 7) but is a **ucm** fix — dcm already emits them
  correctly.
- Do **not** attempt to fix live WS push. That is jg MSG-9 finding 1, still undiagnosed, and its home
  is not established.
- No new models, no migration. `last_delivered_at` may stay on the model even if C removes its use —
  dropping a column is a separate guarded change.
- Do not touch encryption, policy hooks, or `resolve_recipients`.

## RISKS
- **PII surface.** `display_name` and `username` become visible to every conversation participant.
  Confirm that is acceptable for the strictest existing scope (broadcast to all event participants).
  Participants already see each other via DM candidate lists and `other_user_id` resolution, so this is
  a consolidation rather than a new exposure — but `sec_reviewer` must confirm it, not assume it.
- **N+1 on list endpoints** — see scope B. This is the most likely way to ship a performance regression.
- **Cross-app blast radius.** Every app on dcm consumes this serializer. The change is additive
  (scope A), so existing readers are unaffected; scope C is **not** additive and needs the version bump.
- A `sender.id` whose type differs from `currentUser.id` reproduces the original bug while looking
  fixed. Test 1 must compare types, not just presence.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. `serialize_message` includes `sender` with `id`/`display_name`/`username`, and `sender.id` **equals**
   `sender_id` **and has the same type**. Assert on both serializers.
2. The conversation-messages list endpoint returns `sender` on every row, and its **query count does not
   scale with the number of messages** (the N+1 guard — follow the existing pattern in
   `test_platform_launchers_query_count_does_not_scale_with_event_count`).
3. A realtime `message` frame carries the same `sender` shape as the REST payload (the two paths must
   not diverge — that divergence is what produced this whole finding family).
4. If scope C is approved: `read_status` no longer returns `delivered_count`, and `all_read` still
   flips correctly when every non-sender participant has read.

**Non-vacuity, mandatory:** for test 1, assert on a message **loaded from the database**, never one the
test just constructed in memory with a sender attached. The bug this WO fixes is invisible to a test
that builds its own payload — that is precisely how it survived review. Prove each test fails with the
change reverted.

## TEST SCOPE FOR THE GATE (orchestrator)
`messaging/` only. Note the documented pre-existing baseline failures in this suite (12-13, drifting) —
compare before/after via `git stash` and require delta = 0, and **state the baseline count you measured**
rather than quoting a previous WO's number.

## OPERATOR DECISIONS (this run)
- **Scope C is approved to implement now, without a version bump.** Drop `delivered_count` from
  `read_status`'s response and delete `mark_delivered` per the WO text. Record it as a breaking change
  in `CHANGELOG.md` under an "Unreleased" heading (do not touch `pyproject.toml`'s `version`). Do not
  publish/tag this repo — that is a separate step outside this WO.
- **Scope F (new, operator-approved extension of this WO, additive only): add `message_id` to the poll
  REST response.** `serialize_poll` (`serializers.py:53-65`) and `_poll_response` (`views.py:32-36`) never
  emit the poll's message id, even though `Poll.message` is a `OneToOneField` (`models.py:209`) — confirmed
  by direct inspection, not assumed. `ui-core-micha` MSG-6e scope B needs a stable way to key a freshly
  created/voted/closed poll to its message; today there is none, which is exactly the contract gap that
  WO's scope B anticipates and is instructed to "stop and report" on. Add `"message_id": str(poll.message_id)`
  to `serialize_poll`'s returned dict (or add it in `_poll_response` if you judge the realtime `poll_updated`
  frame — which already carries `message_id` at the frame level via `_poll_updated_payload`, see
  `services.py:391-394` — should not duplicate it inside the embedded `poll` object; use your judgment, but
  state which you chose and why). This is additive (a new key), so it needs no version bump either. Add one
  narrow test: creating/voting/closing a poll returns a `message_id` equal to the message it was created on.

## IMPLEMENTATION MAP (Orchestrator)

### Context package
- **`src/django_core_micha/messaging/serializers.py`**
  - `serialize_message` (function starts `:80`, returned dict `:83-95`): add a `"sender"` key —
    `{"id": message.sender_id, "display_name": <derived>, "username": message.sender.username}`. `id` must
    be `message.sender_id` itself (already an int) — do not read `message.sender.id` separately, they are
    the same value but this avoids a second attribute touch and keeps the type identical to `sender_id`.
  - `serialize_last_message` (`:104-119`, the "compact variant" the WO's `:118` line number refers to —
    that is the `"sender_id": message.sender_id` line inside this function, not a second `serialize_message`
    copy): add the same `"sender"` shape. Its queryset at `:105`
    (`conversation.messages.select_related("conversation__app", "poll")`) must also
    `select_related("sender")` — it already does one query per conversation for this `.first()` call, so
    this is a free join, not a new query; without it, adding `sender` here reintroduces exactly the N+1
    scope B warns about, just in `serialize_conversation_core`'s conversation-list path instead of the
    message-list path.
  - **No existing `display_name` helper exists anywhere in this repo** (verified: `grep -rn display_name
    --include=*.py` across the whole tree returns nothing). The closest analog, `get_greeting_name`
    (`emails/__init__.py:39-47`), is email-greeting-specific (falls back to "there"), not a UI presentational
    name — do not reuse it verbatim. Write a small local helper in `serializers.py`: prefer
    `user.get_full_name().strip()`, then fall back to `user.username` — do NOT fall back to email (a
    message sender's display name reaching every conversation participant, incl. broadcast, must not leak
    an email address). State in your final report that you wrote a new helper here, since none existed.
  - **Scope F**: `serialize_poll` (`:53-65`) — add `message_id` per the operator decision above.
- **`src/django_core_micha/messaging/views.py`**
  - `ConversationMessagesView.get` (`:198-201`) and `ThreadView.get` (`:335-339`, read the surrounding
    lines yourself) **already** `select_related("conversation__app", "sender")` on their querysets — the
    N+1 guard for scope B's list endpoints is already in place; you do not need to add it there. Write the
    regression test (required test 2) against the existing call, not a new `select_related`.
  - `realtime.py`'s `publish_messaging_event` (`:9-19`) also already `select_related(..., "sender")` at
    `:17` before calling `serialize_message` — the realtime `message`/`message_edited` frame path needs no
    change either; this is what required test 3 (REST/frame parity) exercises.
- **`src/django_core_micha/messaging/services.py`**
  - `read_status` (`:343-352`): remove the `delivered_count` line (`:346`) and its key from the returned
    dict (`:348`). Leave `all_read` and `recipient_detail` untouched.
  - `mark_delivered` (`:297-303`): delete the whole function. Confirmed zero production callers — no view,
    URL, WS handler, or signal reaches it (`grep -rn mark_delivered --include=*.py` outside
    `services.py`/`tests/test_services.py` returns nothing).
  - `src/django_core_micha/messaging/tests/test_services.py`: `test_watermark_frames_fire_only_on_actual_advance`
    (around `:374-401`) calls `mark_delivered` three times and asserts the frame-type set includes
    `"delivered"` and `len(sent) == 3` in two places. Remove the `mark_delivered`/`delivered_at` calls and
    update the set/length assertions to `{"read_state", "thread_read_state"}` / `2` — this is a direct,
    expected consequence of deleting the function, not new test-writing scope.
- **`CHANGELOG.md`**: add an entry for the `sender` addition (additive) and the `delivered_count` removal
  + `mark_delivered` deletion (breaking, no version bump this run — note that explicitly).

### Do-not-touch reminders (from the envelope, restated)
`serialize_conversation`/`serialize_conversation_core` beyond the `select_related` addition above; no new
models/migrations; no realtime frame-type changes; no touching `encryption`/`policy`/`resolve_recipients`.

### Target repo working directory
`C:\Users\biglmi\Documents\webapps\django-core-micha` (git root — no `backend/`/`frontend/` split in this repo).

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `develop` if it exists, else `main`.
Publish + version bump per the repo's release flow; jg's pin bump is **not** part of this WO.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
