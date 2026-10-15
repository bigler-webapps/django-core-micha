# DCM-CHAT-6 — Chat list shows active chats as active, archived as archived

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found live on staging in `cockpit/CP-CHAT-4`. Released together with `DCM-CHAT-4` and `DCM-CHAT-5`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The chat list endpoint filters by archive state the wrong way round (`assistant/views.py`, `ChatListCreateView.get`: `archived_at__isnull=archived`). The default list returns the archived chats, and the archived filter returns the active ones. On cockpit staging, all eight chats had no archive date, yet after a reload the active list was empty and the "Archived" entry showed eight. The ucm tests use a mocked API, so nothing caught it.

Expected outcome: the default list returns exactly the owner's non-archived chats, and the archived filter returns exactly the owner's archived chats. Cursor pagination works within each list.

## Scope

- Correct the archive filter of the chat list.
- No change to the response shape, the cursor format, ownership or any other endpoint.

## Non-goals

- Other list behaviour (ordering, page size), the archive and restore actions themselves, and ui-core-micha.

## Tier

Tier 3: a change inside shared-core.

## Risks

- **Cursor context:** the cursor carries the archive flag in its context. A cursor from one list must not page into the other. The existing behaviour stays.

## Required tests to write

- With one active and one archived chat of the same owner: the default list returns only the active one, and the archived filter returns only the archived one.
- Another user's chats appear in neither list.
- Pagination: with more active chats than one page, following the cursor stays within the active list.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Another implementer run is editing OTHER files in the same working tree at the same time (`assistant/drivers/base.py`, `assistant/drivers/claude_cli.py`, `assistant/turns.py`, `docs/design/assistant-chat.md`, new files `assistant/tests/test_tool_handlers.py` and `assistant/tests/test_cli_pre_init.py`): do not touch any of them and preserve their edits.

## Context package

- **Seam:** `src/django_core_micha/assistant/views.py`, `ChatListCreateView.get` (line ~53-57): `archived=request.query_params.get("archived","false").lower() in {"1","true","yes"}` and then `qs=AssistantChat.objects.filter(owner=request.user,archived_at__isnull=archived)`. `archived_at__isnull=True` selects NON-archived chats, so the flag is inverted: for `archived=True` the filter must select `archived_at__isnull=False`, for `archived=False` it must select `archived_at__isnull=True` (that is `isnull=not archived`). Leave the cursor context (`_cursor_context("chats", user, archived=archived, limit=limit)`), the response shape, ordering, the in-Python scope filter and everything else exactly as they are.
- **Tests:** a NEW file `src/django_core_micha/assistant/tests/test_chat_list.py`. Imitate `test_published_ucm_wire_shapes` and `test_rest_foreign_owner_and_archived_write_refused` in `assistant/tests/test_core.py` (APIRequestFactory, `force_authenticate`, `ChatListCreateView.as_view()`), and its autouse `catalogue_settings` fixture and `user`/`chat` fixtures (copy what you need, do not edit `test_core.py`). Cases: one active and one archived chat of the same owner (`archived_at=timezone.now()`): default list returns only the active one, `?archived=true` returns only the archived one; another user's chats appear in neither; pagination: more active chats than one page (`?limit=2` with 5 active chats and 1 archived), following `next` stays within the active list and collects exactly the 5 active chats in order. The response keys are `results`, `next`, `has_more`.
- **Docs/changelog/version:** do not edit `CHANGELOG.md`, `pyproject.toml` or `docs/` (the Orchestrator releases; the doc already describes the intended behaviour).

## Required tests

As in Part A "Required tests to write".

## Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.** Everything below is the Orchestrator's work after you finish.

Gate #1 recorded at django-core-micha@38e49c4. Tier 3 · tests: the three of Part A. Dispatched as a second concurrent codex/gpt-6-luna run (disjoint files from DCM-CHAT-4/5), reviewed and released together with them as patch 3.0.1. Live check on cockpit staging via `cockpit/CP-CHAT-6` after publishing. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19.
