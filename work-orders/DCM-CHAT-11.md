# DCM-CHAT-11 — Chat core for a structured consumer: client context, structured turns with repair rounds, one-shot completions

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Stage 2 of the shared chat for survey_app: it prepares `survey_app/SVA-AIB-45`. Companions: `DCM-CHAT-12` (shared chats in a scope), `ui-core-micha/UCM-CHAT-4`. Builds on `DCM-CHAT-10` (3.1.0).

# A. Envelope — addressed to everyone

## Goal and expected outcome

survey_app's AI questionnaire builder (`survey_app/backend/surveys/services/assistant.py`, `consumers.py`) has three needs the chat core does not cover:
- **Client context:** every message carries the editor's current, possibly unsaved draft from the browser.
- **Structured turns:** the model answers with one JSON object, which the app validates. An invalid draft is sent back for repair, up to two rounds, before the answer is accepted.
- **Single calls:** two calls are not chats at all: a resolution suggestion for a merge conflict, and a translation of the consent text.

Expected outcome: the core accepts an app-validated context with a message, runs structured turns with repair rounds instead of streaming raw text, and offers a single completion call without a chat. All of it goes through the same drivers, credentials, limits and cost guard. Apps that use none of it behave exactly as before.

## Operator decisions this Envelope records (2026-10-15)

- **Client context:** the context travels with the message (`send` frame) and is validated by the app. It is not saved beforehand.
- **Display:** during a structured turn the user sees the typing state and then the finished answer. No raw JSON is streamed.
- **Single calls:** they move into the core. survey_app's own CLI runner goes away entirely.

## Scope

- **Client context:**
  - The `send` frame accepts an optional `context` (a JSON object).
  - The core bounds its size with a new limit `max_client_context_bytes`. An oversized context is rejected with `invalid_frame`.
  - An optional app hook validates the context and may normalise it. If it rejects the context, the send ends with `invalid_frame` before any turn row or provider call.
  - The validated context reaches the per-turn context hook from `DCM-CHAT-10`. Hooks written for 3.1 keep working unchanged.
  - The context is not stored as part of the user text or the history.
- **Structured turns:**
  - With a new app setting, an app declares that its turns are structured. The core then sends no `delta` frames for that app's turns; clients show the typing state until the final `turn` frame.
  - The after-turn hook from `DCM-CHAT-10` may additionally return a repair request with feedback text. The core then sends the feedback to the model as a follow-up within the same turn: a resume for the Claude Code driver, the next message for the API drivers. It calls the after-turn hook again on the new answer.
  - A new limit `max_repair_rounds` (default 2) bounds the rounds. After the last round, the hook must accept, or it supplies its own fallback text and frames.
  - Every round counts against the cost guard and the existing time and size limits. Only the accepted result is stored as the turn's text.
- **Single completion:**
  - A server-side function sends one prompt to one catalogue entry, with optional system text and effort, and returns the text and usage. There is no chat, no persistence and no tools.
  - It uses the credential resolver for the calling user, the cost guard, the provider timeout and the output bound, and maps errors onto the existing taxonomy.
  - For the Claude Code driver it runs in a throwaway session directory that is removed afterwards. It is a Python API for app code, not a REST endpoint.
- **Documentation:** `docs/design/assistant-chat.md`, the `CHANGELOG` and the `webapp-management/SHARED_CAPABILITIES.md` row.

## Non-goals

- Shared chats in a scope (`DCM-CHAT-12`).
- Any survey-specific schema, prompt or validation in dcm.
- Streaming a field out of partial JSON.
- A REST endpoint for single completions.

## Tier

Tier 3: a change inside shared-core that extends the WebSocket contract and adds a provider call path.

## Risks

- **Client context as an input channel:** it is user-supplied data that reaches the model. The size bound and the app's validation hook are the controls. The core never interprets it.
- **Repair loops and spend:** every round costs time and money. `max_repair_rounds`, the cost guard and the provider timeout bound it.
- **Neutrality:** cockpit and maana must see no change. Structured mode, client context and repair are off unless configured or sent.

## Required tests to write

- Client context:
  - an accepted context reaches the per-turn context hook;
  - an oversized one is rejected with `invalid_frame`;
  - a context the app hook rejects ends the send with `invalid_frame`, without a turn row or provider call;
  - 3.1-style context hooks keep working.
- Structured turns:
  - no `delta` frames are sent;
  - a repair request triggers exactly one follow-up per round, up to `max_repair_rounds`;
  - the accepted text is stored;
  - the last round's fallback is used when the hook keeps asking for repair;
  - each round passes through the cost guard.
- Single completion, for each driver with fakes:
  - text and usage are returned;
  - the cost guard is applied;
  - a timeout maps to `timeout`;
  - the Claude Code session directory is removed afterwards.
- Neutrality: without the new settings, the existing assistant tests pass unchanged.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the seams and the shapes Part A leaves open. Everything is optional and neutral when unused (cockpit and maana must see no change). Do NOT write a skeleton: every item must be fully implemented and tested. `DCM-CHAT-12` (shared chats) is implemented in the same run; it owns visibility, the turn sender and the group delivery in `consumers.py` / `views.py` / `access.py`; this order owns the client context, structured turns, repair rounds and `completion.py`. Both edit `consumers.py` and `turns.py`: keep each other's edits.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories.

## Context package (verified against the landed code 3.2.0, paths under `src/django_core_micha/assistant/`)

- `consumers.py` `receive_json`: strict frame shape `set(content) != {"type","message"}` -> `invalid_frame`; `receive` bounds the raw frame to `max_message_length * 4` bytes; the `send` branch runs the archived, `turn_active` and availability checks and then starts `run_turn(self.chat, user, message, self.send_json, authorize=self._session_valid)` in `execute()`.
- `hooks.py`: `_hook(name)`; `build_turn_context(chat, user, message, turn)` (calls `DCM_ASSISTANT_TURN_CONTEXT`), `process_after_turn(chat, user, turn, text)` (calls `DCM_ASSISTANT_AFTER_TURN`) and the dataclass `AfterTurnResult(text=None, frames=())`.
- `limits.py`: `DEFAULTS` + `get_limit(name)` (value must be positive).
- `turns.py` `run_turn(chat, user, message, emit, cancel_event=None, authorize=None)`: builds `prompt`, `context_text`, `history`, `tools` (now `tools_for_chat`), the CLI `prompt_data` JSON (with a `context` key) or the API `messages` (history + last user message with the context prefix), starts the access watcher, streams the driver inside `asyncio.timeout(provider_timeout)` collecting `chunks` (emitting `delta` frames, `tool` frames, `usage`, `session`, `init`), cancels the watcher, runs the after-turn hook once, then `_finish(...)`. All failure branches (CancelledError, TimeoutError, Exception, session discard) and the 3.0.x/3.1.0 behaviours stay exactly as they are.
- `drivers/*`: each driver's `stream(model=..., prompt=..., messages=..., system=..., tools=..., user=..., chat=..., credential=..., effort=..., cancel_event=..., session_id=..., max_tool_calls=...)`; the API drivers go through the cost guard per provider call using `user`; the CLI driver keeps a per-chat session directory (`drivers/claude_cli.py` `_session_dirs(chat_pk, create)`, root from `DCM_ASSISTANT_CLI_ROOT`) and yields a `session` event with the session id at the end of a successful stream.
- `credentials.py` `resolve_credential(user, driver)`; `catalogue.py` `validate(driver, model, effort)`, `entry_by_id`, `selection_of`; `turns._make_driver(name, credential)`.

## Fixed design

1. **Client context.** The `send` frame may carry `"context"`: a JSON object (anything else: `invalid_frame`). Allowed key set becomes `{"type","message"}` or `{"type","message","context"}`. New limit `max_client_context_bytes` (default 65536): measured as the UTF-8 length of `json.dumps(context, separators=(",", ":"), ensure_ascii=False)`; larger -> `{"type":"error","code":"invalid_frame"}`. Raise the raw-frame bound in `receive` to `max_message_length * 4 + max_client_context_bytes`. Optional app hook `DCM_ASSISTANT_CLIENT_CONTEXT(chat, user, context) -> dict` (called in `receive_json` via `sync_to_async`, before the archived/turn checks create anything): it returns the normalised context; ANY exception raised by it (including `ValueError`) ends the send with `invalid_frame` before a turn row exists and before any provider call. Without the hook the context passes through unchanged. `run_turn` gains `client_context=None`; the (normalised) context reaches the per-turn context hook: `build_turn_context(chat, user, message, turn, client_context=None)` passes `client_context=` only when the hook's signature declares a parameter of that name (or `**kwargs`), so hooks written for 3.1 with four positional arguments keep working. The context is never stored (not in `user_text`, not in history) and the core never interprets it.
2. **Structured turns.** App setting `DCM_ASSISTANT_STRUCTURED_TURNS` (bool, default False). When true, `run_turn` emits NO `delta` frames for that app's turns (it still collects the text, applies `max_output_bytes`, and emits `tool` frames, notices, `app` frames and the final `turn` frame as before); clients show the typing state until the final frame.
3. **Repair rounds.** Add `repair: str | None = None` to `AfterTurnResult`. When the after-turn hook returns a result with `repair` set (non-empty feedback text) and fewer than `get_limit("max_repair_rounds")` (new limit, default 2) repair rounds have been used in this turn, the core sends the feedback to the model as a follow-up INSIDE the same turn and calls the hook again with the new answer: Claude Code driver: stream again with `session_id` = the session id yielded by the previous round and `prompt` = a JSON object `{"message": <feedback>}` (resume); API drivers: `messages` = previous messages + `{"role":"assistant","content": <previous text>}` + `{"role":"user","content": <feedback>}` (system prompt and tools unchanged). Each round is a normal driver call: it goes through the cost guard (API drivers do this themselves), runs inside its own `asyncio.timeout(provider_timeout)` and its own `max_output_bytes` budget, and the access watcher keeps running across rounds. When the rounds are exhausted and the hook still returns `repair`, the core ignores the `repair` and uses that result's `text` and `frames` as the fallback (if the result has no text the last raw answer is stored). Only the accepted (final) text is stored as `assistant_text`; `app` frames come only from the accepted/fallback result; `usage` is the sum over rounds. Session bookkeeping (`session_tokens`, `session_id`) follows the last CLI round. A raising hook in any round keeps the last raw text and completes the turn as it does today. Add `max_repair_rounds` to `limits.DEFAULTS`.
4. **Single completion.** New module `completion.py`: `async def acomplete(user, *, prompt, system="", entry=None, driver=None, model=None, effort=None) -> CompletionResult` and a synchronous wrapper `complete(...)` (via `asgiref.sync.async_to_sync`), `CompletionResult(text: str, usage: dict)`. The target is a catalogue entry: either `entry` (the id `"<driver>:<model>"` from `catalogue.entry_id`) or `driver` + `model`; `effort` defaults to the entry's first allowed effort; the selection is validated with `catalogue.validate` (`DriverError("invalid_selection")` on a mismatch). It resolves the credential with `resolve_credential(user, driver)` (once), builds the driver with `_make_driver`, calls `driver.stream(model=..., prompt=<json for CLI>, messages=[{"role":"user","content":prompt}], system=system, tools=[], user=user, chat=None-or-throwaway, credential=..., effort=..., cancel_event=None, session_id=None, max_tool_calls=...)` inside `asyncio.timeout(get_limit("provider_timeout"))`, concatenates `delta` text, applies `max_output_bytes`, returns text and the `usage` event. Errors map onto the existing `DriverError` taxonomy (`timeout`, `provider_error`, `quota_exceeded`, `not_configured`, `invalid_output`, ...); the API drivers already log provider failures. For the Claude Code driver pass a throwaway chat stand-in (`SimpleNamespace(pk="completion-<uuid4>")`, as the CLI tests do) and remove the directory `_session_dirs(pk)[0]` in a `finally` (the removal must also happen on error and on timeout). No persistence, no tools, not a REST endpoint, no new setting. Add `completion.py` to the design doc as a Python API for app code.
5. **Docs:** `docs/design/assistant-chat.md` (the `send` frame with `context`, the `invalid_frame` cases, the hook, structured mode, the repair contract incl. exhaustion rule, the two new limits, the completion API). Do NOT edit `CHANGELOG.md`, `pyproject.toml`, `WORK_ORDERS.md` or `webapp-management` (the Orchestrator releases).

## Tests (new files only: `assistant/tests/test_client_context.py`, `assistant/tests/test_structured_turns.py`, `assistant/tests/test_completion.py`)

Imitate `assistant/tests/test_turn_hooks.py` and `test_access_recheck.py` (fake drivers through `run_turn`, consumer unit tests with mocked `send_json`), `assistant/tests/test_driver_logging.py` and `test_core.py` for fake provider clients and cost-guard patching (`monkeypatch` of `_reserve` / `settle_reservation` / `release_reservation`), and the `_cli_proc` fake-process helper (`test_core.py` / `test_cli_pre_init.py`) for the CLI completion. Cover every bullet of Part A "Required tests to write": context accepted and visible to a hook that declares `client_context`, a legacy four-argument hook unchanged, oversized -> `invalid_frame`, rejecting app hook -> `invalid_frame` with no turn row and no provider call; structured mode emits no `delta` but the final `turn` frame carries the text; repair: exactly one follow-up call per round up to `max_repair_rounds`, accepted text stored, exhaustion fallback used, each round reserves against the cost guard (API fake client); completion for each driver with fakes (text and usage returned, cost guard applied, timeout -> `DriverError("timeout")`, CLI session directory removed after success, error and timeout); neutrality (the existing assistant tests are part of the Orchestrator's run). Run only your new test files with the interpreter from the dispatch note.

## Invariants
Off or neutral without the new settings/hooks/frame keys; no provider call before validation of the context; no raw JSON streamed in structured mode; repair never exceeds `max_repair_rounds`; credentials and cost guard always use the calling user; nothing stored from the client context.

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

Gate #1 recorded at django-core-micha@465767f. Tier 3 · tests: the groups of Part A. Implemented together with `DCM-CHAT-12` in one codex/gpt-6-luna dispatch (high effort) on top of 3.2.0 (`DCM-CHAT-9` landed first, as sequenced with the other sessions). Orchestrator-owned: migration check (`0003_turn_sender` is written by the implementer for CHAT-12), CHANGELOG + minor version 3.3.0, `webapp-management/SHARED_CAPABILITIES.md` row, review lenses (envelope, regression, duplication, tests) plus security over the combined diff and the review repeated on the final diff after fixes, register rows, publish observed. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19 (consumer: survey_app/SVA-AIB-45).
