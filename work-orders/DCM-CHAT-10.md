# DCM-CHAT-10 — Chat core hooks for a research consumer: per-turn context, after-turn processing, availability, live access checks, session upkeep

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Stage 2 of the shared chat: it prepares `maana/MAANA-CHAT-11` (maana moves its assistant onto the core). Companion: `ui-core-micha/UCM-CHAT-3` (surface extension points).

# A. Envelope — addressed to everyone

## Goal and expected outcome

maana's research assistant (`maana/backend/assistant/`) was the template for the chat core. It cannot move onto the core yet, because the core offers only three app hooks: system prompt per chat, read-only tools and a scope permission. maana additionally needs five things:
- context computed per message;
- a record of what was sent;
- processing of the answer (change proposals) with its own live frames;
- an availability gate (research data approval);
- an access check while a turn runs.

Some of maana's session upkeep is also missing from the core.

Expected outcome: the core offers these as generic, optional hooks and core behaviours. maana can then delete its own chat plumbing and keep only its domain logic. Apps that use none of the new hooks, such as cockpit, behave exactly as before.

## Operator decisions this Envelope records (2026-10-15)

- maana moves onto the core in three orders: this one, `UCM-CHAT-3` and `MAANA-CHAT-11`.
- Where maana's own session and history mechanics go beyond the core, the capability becomes a core feature here. It does not stay a maana special path.

## Scope

**Hook 1, per-turn context:** an optional hook called once per turn, before the provider call.
- It receives the chat, the user, the message and the new turn.
- It returns a context text that the core sends with this turn's prompt, in addition to the system prompt.
- It may persist its own record for that turn, for example what data was disclosed, keyed by the turn id in the app's own tables.
- If the hook raises, the turn fails with `provider_error` and nothing goes to the provider.

**Hook 2, after-turn processing:** an optional hook called once after a turn completed.
- It receives the chat, the user, the turn and the model's text.
- It may return a replacement text that is stored and shown instead of the raw text, for example with a machine-readable block removed.
- It may also return app frames. The core delivers them over the WebSocket before the final `turn` frame, as `{"type":"app","name":<app-defined name>,"payload":<JSON>}`. The core does not interpret `name` or `payload`.
- If the hook raises, the turn still completes with the raw text, and the core logs the failure server-side.

**Hook 3, availability:** an optional hook that receives the user and a scope and returns either available or a reason code chosen by the app.
- `status/` takes an optional `scope` query parameter and reports `"status":"unavailable","reason":<code>` when the hook says so.
- Provider availability is still reported as today.
- A `send` in an unavailable scope is rejected with the error `unavailable`, before any provider call.

**Live access check during a turn:**
- While a turn runs, the core re-evaluates the scope permission at a configurable interval (default 1 second, a new limit `access_recheck_seconds`).
- It also evaluates an optional turn-validity hook, which receives the chat, the user and the running turn. With it, an app can invalidate a running turn when its own permissions narrow.
- If either fails, the turn is cancelled and the socket closes with 4403, as it does today for a failed per-frame check.

**Scoped chat lists:** `GET chats/` accepts an optional `scope` parameter and returns only that scope's chats. The scope permission applies, and a scope the user may not access returns 404. Without the parameter, behaviour is unchanged.

**Session upkeep, as core behaviour for the Claude Code driver:**
- **Token threshold:** a resumed provider session whose accumulated input tokens exceed a new limit `session_rebuild_tokens` is not resumed. The next turn starts a fresh session, rebuilt from the stored history.
- **Prompt version:** an optional app setting `DCM_ASSISTANT_PROMPT_VERSION` is stored with each provider session. When it changes, the next turn starts a fresh session.
- **History budget:** when the history is rebuilt and would exceed `max_prompt_bytes`, the oldest completed turns are left out until it fits, instead of the turn failing with `prompt_too_long`. The client receives `{"type":"notice","code":"history_trimmed","left_out":<count>}`.

**Documentation:** `docs/design/assistant-chat.md`, the `CHANGELOG` and the `webapp-management/SHARED_CAPABILITIES.md` row describe the hooks, the new frame, the new error and reason, the new parameters and the new limits.

## Non-goals

- Anything maana-specific in dcm: grants, projections, aliases, proposals, plans or their schemas.
- Tools that change data. Proposal confirmation stays an app endpoint.
- The UI (`UCM-CHAT-3`) and the maana migration (`MAANA-CHAT-11`).
- New drivers (`DCM-CHAT-2`) and per-user credentials (`DCM-CHAT-3`).
- Tools and system prompt chosen per chat scope. That is `DCM-CHAT-9`, a separate order for cockpit; maana's four lookups apply to every maana chat.

## Tier

Tier 3: a change inside shared-core that extends the WebSocket contract and the access checks.

## Risks

- **Silent behaviour change for cockpit:** every new behaviour must be off or neutral when no hook or setting is configured. The history budget and the token threshold apply only when they are reached.
- **App frames as a side channel:** the payload is app data sent to the chat owner only, over the existing owner-checked socket. No other path is added.
- **Access check cost:** a check every second runs database queries during long turns. The interval is configurable, and the check runs only while a turn is in progress.
- **Hook failures:** a raising hook must neither leak provider details nor leave a turn in `generating`. The failure rules above are part of the contract.

## Required tests to write

- Per-turn context:
  - the hook's text reaches the prompt of that turn only;
  - the hook receives the turn id;
  - a raising hook fails the turn with `provider_error`, without a provider call.
- After-turn:
  - the replacement text is stored and sent in the final `turn` frame;
  - app frames arrive before it, in the documented shape;
  - a raising hook keeps the raw text and the turn completed.
- Availability:
  - `status/?scope=` reports `unavailable` with the app's reason;
  - `send` in that scope returns `unavailable`, and no provider call happens.
- Live access check: a scope permission or turn-validity hook that starts failing during a running turn cancels it and closes with 4403 within the interval.
- Scoped list: `?scope=` returns only that scope's chats, and a forbidden scope returns 404.
- Session upkeep:
  - the token threshold forces a fresh session;
  - a changed prompt version forces a fresh session;
  - a history over budget leaves out the oldest turns and sends `history_trimmed` with the count.
- Neutrality: with none of the new hooks or settings configured, the existing assistant tests pass unchanged.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the seams and the shapes Part A leaves open so that `ui-core-micha/UCM-CHAT-3` and `maana/MAANA-CHAT-11` can be built against them. Everything is optional and neutral when unused (cockpit must behave exactly as before). Do NOT write a skeleton: every item below must be fully implemented and tested.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch `ui-core-micha`, `maana`, `cockpit` or `webapp-management`.

## Context package (verified against the landed code, all paths under `src/django_core_micha/assistant/`)

- `hooks.py`: `_hook(name, default)` reads `DCM_ASSISTANT_<NAME>` from settings (dotted path string or callable); `build_system_prompt`, `tool_registry`, `can_access_scope(user, scope)` (default: allowed only for an empty scope). Add the new hooks here in the same style.
- `limits.py`: `DEFAULTS` dict + `get_limit(name)` (override via `DCM_ASSISTANT_LIMITS`). Add `access_recheck_seconds: 1` and `session_rebuild_tokens: 200_000`.
- `models.py`: `AssistantChat` has `session_driver`, `session_reference`. Add `session_tokens` (`PositiveIntegerField`, default 0) and `session_prompt_version` (`CharField(max_length=64, blank=True, default="")`). Write the migration by hand as `migrations/0002_session_upkeep.py` (two `AddField` operations, dependency on `0001_initial`), because the tests run with migrations; the Orchestrator verifies it with `makemigrations --check`.
- `turns.py`: `_start`, `_finish(turn_id, text, state, error="", usage=None, trace=None, session_id=None)`, `run_turn(chat, user, message, emit, cancel_event=None)`, `turn_payload`, `_emit_final`. `run_turn` builds `prompt`, `history`, `session_file` (resume only when the CLI session `.jsonl` exists), `prompt_data` (JSON for the CLI prompt; checks `max_prompt_bytes`), sends `notice history_rebuilt`, builds `kwargs` (`messages=history + [user message]` for the API drivers), streams inside `asyncio.timeout(provider_timeout)`, then `_finish(..., "completed", ..., session_id=session_id)` and emits the final `turn` frame. The failure branches (CancelledError / TimeoutError / Exception) are covered by existing tests; keep their behaviour.
- `consumers.py`: `ChatConsumer.receive` (frame rate 4429, `_session_valid` 4401, `get_object` + `can_access_scope` 4403) and `receive_json` (stop / send, archived check, `execute()` wrapper that calls `run_turn(...)` and maps exceptions to `{"type":"error","code":...}`).
- `views.py`: `AvailabilityView` (route `assistant/status/`, uses `availability.availability()`), `ChatListCreateView.get` (list with cursor; owner filter; in-Python scope filter), `CatalogueView`.
- `history.py`: `build_history(chat)` returns role/content dicts, two per completed turn.
- `drivers/claude_cli.py` yields `{"type":"usage","usage":{"input_tokens":..,"output_tokens":..}}` and `{"type":"session","session_id":..}`; API drivers yield `usage` too.

## Fixed shapes

**Settings and hooks (all optional; names resolved with `_hook`; sync callables, call them via `sync_to_async`):**
- `DCM_ASSISTANT_TURN_CONTEXT(chat, user, message, turn) -> str | None`. Called once per turn after the system prompt is built and before the provider call. The text is added to THIS turn's provider input only and is never stored in `user_text` or history: for the CLI add a `"context"` key to the `prompt_data` JSON; for the API drivers prefix the last user message content with the context text followed by a blank line and a `---` line. A raising hook fails the turn with `provider_error` and no provider call happens (the generic failure branch already does this; add a test).
- `DCM_ASSISTANT_AFTER_TURN(chat, user, turn, text) -> None | str | AfterTurnResult`. Define `AfterTurnResult` (dataclass, `text: str | None = None`, `frames: list = ()`) in `hooks.py`. Called once after the stream ended successfully, before the turn is finished. A `str` or `.text` replaces the stored `assistant_text` (so the final `turn` frame and history carry the replacement). Each frame is `{"name": str, "payload": any JSON-serialisable}`; the core emits `{"type":"app","name":name,"payload":payload}` for each before the final `turn` frame; drop (and log) malformed frames. A raising hook: log with `log.exception`, finish the turn completed with the raw text, emit no app frames.
- `DCM_ASSISTANT_AVAILABILITY(user, scope) -> None | str`. `None` means available, a non-empty string is the app's reason code.
- `DCM_ASSISTANT_TURN_VALIDITY(chat, user, turn) -> bool`.
- `DCM_ASSISTANT_PROMPT_VERSION`: int or str; unset means no prompt-version behaviour.

**Availability surface:** `GET status/?scope=<s>`: when the hook returns a reason, respond `{"status":"unavailable","reason":<code>,"providers":{...}}`; otherwise unchanged (`available`/`not_configured` as today, also unchanged without `scope`). In `ChatConsumer.receive_json`, for a `send` in a chat whose scope the hook marks unavailable, answer `{"type":"error","code":"unavailable","reason":<code>}` before `run_turn` (so no turn row and no provider call). Add `unavailable` to the documented error codes.

**Live access check:** `run_turn` gains an optional `authorize=None` parameter (an async callable returning bool; the consumer passes its `_session_valid`). While the provider stream runs, a watcher task re-checks every `get_limit("access_recheck_seconds")` seconds: `can_access_scope(user, chat.scope)`, the turn-validity hook (if set) and `authorize()` (if given). On a failure the watcher marks the turn as access-lost and cancels the task running `run_turn` (`asyncio.current_task()` captured at the start). The cancelled branch then finishes the turn as `cancelled` as today and, when the access-lost flag is set, raises a new `AccessLost(DriverError)` (code `access_lost`) instead of re-raising `CancelledError`; `ChatConsumer.execute` catches `AccessLost` and calls `await self.close(code=4403)` instead of sending an error frame. The watcher is always cancelled in the `finally` and never runs when no stream is running. A user Stop must still behave exactly as before.

**Scoped list:** `GET chats/?scope=<s>`: if `can_access_scope(user, s)` is false answer 404 (`{"code":"not_found","error":"not_found"}` like the other 404 problems; check how `_chat`/`Http404` is rendered and stay consistent), otherwise return only chats with that scope. The cursor context must include the scope so a cursor does not page into another scope. Without `scope` nothing changes.

**Session upkeep (Claude Code driver only):**
- Token threshold: after a `completed` CLI turn, `chat.session_tokens` is the running sum of `usage.input_tokens` of the turns that used the same provider session (reset to this turn's input tokens when the turn started a fresh session; add when it resumed). Before resuming, if `session_tokens > get_limit("session_rebuild_tokens")` treat the session as not resumable (`session_file = False`): fresh session, history rebuilt, `notice history_rebuilt` as today.
- Prompt version: when `DCM_ASSISTANT_PROMPT_VERSION` is set, store `str(version)` in `session_prompt_version` whenever a CLI session id is stored; if the stored value differs from the configured one, do not resume (same path). When the setting is unset, ignore the field.
- History budget: where the history is rebuilt (no resumable session, and for the API drivers every turn), if `prompt_data` would exceed `max_prompt_bytes`, drop the oldest completed turns (two history rows each) until it fits and emit `{"type":"notice","code":"history_trimmed","left_out":<number of dropped turns>}`; only when nothing is left to drop does it fail with `prompt_too_long` as today. Apply the same trimmed history to `messages` for the API drivers.

## Docs
`docs/design/assistant-chat.md`: document every hook, the `app` frame, the `unavailable` error and reason, `status/?scope=`, `chats/?scope=`, the two new limits, the prompt-version setting and the two notices. Do NOT edit `CHANGELOG.md`, `pyproject.toml`, `WORK_ORDERS.md` or `webapp-management` (the Orchestrator releases and writes the capability row).

## Tests
New files only (do not edit existing test files except where an existing assertion must change because of a deliberate new field): `assistant/tests/test_turn_hooks.py` (context, after-turn, raising hooks, availability incl. `send`), `assistant/tests/test_access_recheck.py` (watcher, 4403, Stop unchanged), `assistant/tests/test_scope_list_status.py`, `assistant/tests/test_session_upkeep.py` (threshold, prompt version, history trimming). Imitate `assistant/tests/test_cli_pre_init.py` and `test_pre_init_timeout.py` for fake drivers run through `run_turn` and `override_settings(DCM_ASSISTANT_LIMITS=...)`, `assistant/tests/test_core.py` for REST (`APIRequestFactory`, `force_authenticate`) and consumer tests (`ChatConsumer` with mocked `send_json`/`close`). Cover every bullet of Part A "Required tests to write", including that a stop frame still cancels and that, with no new hook or setting configured, the existing assistant tests pass unchanged (they are part of the Orchestrator's run).

## Invariants
Hooks never receive credentials; app frames go only to the owner's socket via the existing `emit`; nothing here may change behaviour when no new hook or setting is configured; a raising hook never leaves a turn in `generating`; do not weaken any existing check (init fail-closed, ownership, archived refusal, rate limit 4429).

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

Gate #1 recorded at django-core-micha@fa2ba9d. Tier 3 · tests: the groups of Part A. One codex/gpt-6-luna dispatch at high effort (the 2026-10-15 first run of DCM-CHAT-1 returned a skeleton at medium effort, so the map is explicit and a continuation is expected to be possible). Orchestrator-owned: migration check, `CHANGELOG` + minor version 3.1.0, `webapp-management/SHARED_CAPABILITIES.md` row, review lenses (envelope, regression, duplication, tests) plus security over the combined diff, register row, publish observed. `DCM-CHAT-9` (scope-aware tools) has no Envelope yet: implement this order alone and let DCM-CHAT-9 rebase onto 3.1.0. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19; UCM-CHAT-3 builds against the shapes above in parallel.
