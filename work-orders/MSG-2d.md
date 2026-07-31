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
   - `serialize_message(message)` (currently builds the dict directly): add two viewer-independent
     keys, `reply_count` and `last_reply_at` (`null` when none). **A soft-deleted reply still
     counts** — count/aggregate over `message.replies.all()` unconditionally, do not filter out
     `deleted_at__isnull=False` rows. Support both an annotated fast path and an unannotated fallback,
     so the same function works for both list views (annotated, no extra query) and single-object
     views (one small extra query is fine — that's not the N+1 case this WO guards against). Concretely
     something like: if the `message` instance already carries annotated `reply_count`/`last_reply_at`
     attributes (from a queryset `.annotate(...)` — see views.py below), use them directly; otherwise
     compute via `message.replies.aggregate(Count("id"), Max("created_at"))`. Do not add
     `thread_last_read_at` here — that field is viewer-specific and must never appear in
     `serialize_message`'s output (it is added by REST views only, see below) or it will leak into the
     `message`/`message_edited` realtime frames the exact way the poll amendment forbids for
     `voted_option_ids`.
   - `serialize_conversation_core(conversation)`: add `"external_key": conversation.external_key`
     (already a plain, non-encrypted `CharField` on the model — no decryption needed, it is
     "app-supplied, non-sensitive and already visible to every participant by construction" per the
     design amendment). This lands in `serialize_conversation` (REST) and the `conversation_upsert`
     realtime frame automatically since both already build on `serialize_conversation_core` — no
     separate wiring needed there.

2. **`views.py`** — `thread_last_read_at` is viewer-specific and REST-only, added as a
   post-processing step on top of `serialize_message()`'s viewer-independent output at every REST call
   site that returns a message to a specific `request.user` (the same *pattern* MSG-2c used for
   `voted_option_ids` on top of `serialize_poll()` — not literally the same URLs, poll and thread
   receipts are unrelated fields). Two shapes needed:
   - **Single-object sites** (one extra query each is fine, this is not the N+1 case): the `POST`/`GET`/
     `PATCH` message endpoints and the reaction endpoint — `ConversationMessagesView.post` (~167),
     `ConversationAttachmentView.post` (~205), `MessageDetailView.get`/`.patch` (~227/231), `ReactionView.post`
     (~242). Each needs `thread_last_read_at` = the requesting user's own
     `MessageThreadReceipt.objects.filter(root=message, user=request.user).values_list("last_read_at",
     flat=True).first()` (or equivalent), merged into the dict `serialize_message()` returns.
   - **List sites — must be ONE bulk query for the whole page, not one per row**:
     `ConversationMessagesView.get` (~161) and `ThreadView.get` (~299), both currently built on the
     shared `_page(request, queryset, serializer)` helper (~65), which applies `serializer(row)` to
     each row after slicing. Extend `_page` (or wrap its call) so a view needing per-page,
     per-viewer enrichment can bulk-fetch once *after* the page's rows are known (so it's bounded by
     `limit`, not by conversation size) and merge into each row's serialized dict — e.g. one
     `MessageThreadReceipt.objects.filter(user=request.user,
     root_id__in=[row.id for row in rows]).values_list("root_id", "last_read_at")` turned into a dict,
     then `data["thread_last_read_at"] = receipts.get(row.id)` per row. The exact refactor shape of
     `_page` (an optional enrich-callback parameter, a small wrapper, whatever reads cleanest) is your
     call — the constraint is one query for the receipts regardless of page size, not the mechanism.
   - **The two `_page` queryset annotations, to make `reply_count`/`last_reply_at` free (no extra query
     at all) on both list endpoints**: `ConversationMessagesView.get`'s queryset (`Message.objects.filter(conversation=conversation,
     reply_to__isnull=True)...`) and `ThreadView.get`'s queryset (`Message.objects.filter(reply_to=root)...`
     — its rows are themselves depth-1 replies and therefore structurally always have zero replies of
     their own, but they still flow through `serialize_message()` and must carry the field without
     per-row queries). Add `.annotate(reply_count=Count("replies", distinct=True),
     last_reply_at=Max("replies__created_at"))` to both querysets (needs `from django.db.models import
     Count, Max` — `Count`/`Q` are already imported in this file, `Max` is not). `distinct=True` on
     `Count` guards against row multiplication if a future join is added alongside this annotation;
     harmless here.

### Invariants / do-not-touch / pitfalls

- **Viewer-independence is still the load-bearing constraint.** `thread_last_read_at` must never appear
  in `serialize_message`'s return value, nor anywhere in `realtime.py`'s frame construction (which
  calls `serialize_message` directly — do not touch `realtime.py` for this WO, its existing call is
  already correct as long as `serialize_message` itself stays clean). The required test asserts this
  for `message` and `message_edited` specifically, exactly like the existing `voted_option_ids`
  assertion from MSG-2c.
- **No schema change.** `MessageThreadReceipt`/`Message.replies` (the `reply_to` reverse relation) and
  `Conversation.external_key` already exist — this WO is serialization/annotation only. If anything
  seems to need a new field or migration, stop and return to the operator.
- **No N+1** — this is Part A's named risk with a mandatory test. The two list endpoints must each
  issue a bounded, page-size-independent number of queries (annotate for `reply_count`/`last_reply_at`,
  one bulk query for `thread_last_read_at`) — not deferred per-row lookups.
- **Soft-deleted replies still count** — do not add a `deleted_at__isnull=True` filter to the
  `reply_count`/`last_reply_at` computation (annotated or fallback path); a deleted reply keeps its row
  and is rendered as a tombstone, so excluding it would undercount against what the thread displays.
- **No new realtime frame, no change to existing frame payloads beyond what `serialize_message` already
  carries** — MSG-2c's frame set is complete; do not touch `realtime.py`'s frame vocabulary.
- Keep the existing `message`/`message_edited`/`message_deleted`/`poll_updated`/etc. frames otherwise
  byte-compatible — the only permitted change to any frame's payload is the two new viewer-independent
  `serialize_message` keys flowing through unchanged.

### Required tests to WRITE (Codex writes them; the ORCHESTRATOR runs them)

Per Part A "Required tests to WRITE" — extend `tests/test_serializers.py` (reply_count/last_reply_at
correctness incl. the soft-deleted-reply-still-counts case; `external_key` presence per kind),
`tests/test_services.py` (viewer-independence: `thread_last_read_at` absent from `message`/
`message_edited` frames, alongside the existing poll assertion) and `tests/test_views.py`
(`thread_last_read_at` is per-viewer via REST; the N+1 assertion — `django.test.utils.CaptureQueriesContext`
or `assertNumQueries` around a paginated messages/thread fetch with several rows). Follow each file's
existing fixture/style conventions (`domain`/`api_domain`/`serializer_domain`) rather than inventing new
ones.

### Release (do last, after tests are written and passing)

- Version bump in `pyproject.toml`: **minor** (additive contract fields, same precedent as 2.36.0/MSG-2
  and 2.37.0/MSG-2c) → `2.38.0`.
- `CHANGELOG.md` entry under `## [2.38.0] — 2026-07-31`, `### Added`, titled `MSG-2d — readable thread
  reply state, managed-conversation identity`. Summarize: `reply_count`/`last_reply_at` on
  `serialize_message` (soft-deleted replies still counted), `thread_last_read_at` as a viewer-specific
  REST-only addition, `external_key` on `serialize_conversation` — same density as the `[2.37.0]` entry,
  not a line-by-line diff dump.

### Preamble (append verbatim to the Codex prompt)

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`,
> and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests the
> `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute and
> pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review). Run tests
> with `PYTHONPATH=.` from the repo root (e.g. `PYTHONPATH=. pytest -q src/django_core_micha/messaging/tests`)
> — a bare `pytest` with no `PYTHONPATH` cannot even import `tests.settings` in this repo (a known,
> already-fixed-elsewhere gap — DX-2 — not something to rediscover or work around here). The
> orchestrator re-runs the authoritative set + does the independent review after you finish — those are
> the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.
