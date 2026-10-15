# DCM-CHAT-9 — Scope-aware tool set for the chat core

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Needed by `cockpit/CKP-REM-6` (the PR chat). Starts after `DCM-CHAT-10` has landed: both orders change `assistant/hooks.py` and `assistant/turns.py`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The chat core offers the app's tools as one project-wide list: `tool_registry()` in `assistant/hooks.py` takes no argument and `run_turn` in `assistant/turns.py` passes that list to every chat. A chat scoped to one object, such as one pull request in the cockpit, therefore cannot be limited to its own read-only tools; it would also see the project's global tools.

Expected outcome: a project can return the tool list per chat, so a chat in one scope is offered exactly the tools that scope grants and nothing else. A project that does not configure the new hook behaves exactly as before.

## Operator decisions this Envelope records (2026-10-15)

- The scope-aware tool hook is built in the chat core first (this order), then the PR chat follows as `cockpit/CKP-REM-6`.
- Tools stay read-only by contract.
- Backwards compatible: `cockpit` (knowledge-base tool), `maana` and `survey_app` register one global list today and change nothing.

## Decisions taken in this Envelope (overridable by the operator)

- **New optional hook** `DCM_ASSISTANT_TOOLS_FOR_CHAT(chat) -> iterable[ToolSpec] | None`, resolved like the other hooks (dotted path or callable). The chat carries its scope (`chat.scope`).
- **When the hook is not configured**, `DCM_ASSISTANT_TOOL_REGISTRY` applies to every chat, scoped or not, exactly as today.
- **When the hook is configured**, it decides for every chat:
  - A returned list is exactly that chat's tool set, including an empty list (no tools).
  - `None` means "the global registry" for an unscoped chat (empty scope) and "no tools" for a scoped chat. A scoped chat never inherits global tools by default; a project that wants one grants it by returning it.
- **The returned list is validated** exactly like the global registry (valid and unique names, dict input schemas), and an invalid list fails the turn the way an invalid registry does today. The hook's raising fails the turn with `provider_error`, with no provider call.
- **The system prompt is already per chat** (`build_system_prompt(chat)`), so no new prompt hook is added; the design doc states that a project returns a different prompt per scope from that hook.
- **Tool handlers already receive the chat** (`handler(user, chat, arguments, trust_class)`); the contract is unchanged. A handler can read `chat.scope` to bind to its object. The design doc says so and a test pins it.
- **A tool the model asks for that is not in the chat's offered set is refused** by every driver, whatever the global registry contains. This is the security boundary and is tested per driver.

## Scope

- The hook, its resolution and validation in `assistant/hooks.py`, and its use in `run_turn` in place of the global `tool_registry()` call.
- The drivers receive only the chat's tool set (they already take `tools` per call); confirm and test that each driver executes only tools from that list.
- Documentation: `docs/design/assistant-chat.md` (the hook, the `None` rule, the per-scope prompt through the existing builder, handlers receive the chat), the `CHANGELOG`, and the `webapp-management/SHARED_CAPABILITIES.md` row.
- A minor release, published from `main` by the repo's publish flow; the version follows whatever `DCM-CHAT-10` released (3.1.0 planned), so this order releases the next minor.

## Non-goals

- Any cockpit, PR or GitHub specific code in dcm.
- Tools that change data; per-user or per-tool permissions beyond the scope; changing `can_access_scope`.
- The cockpit pin bump and the PR tools themselves (`cockpit/CKP-REM-6`).
- Changing maana's or survey_app's configuration.

## Tier

Tier 3: a change inside shared-core on the security boundary of a chat (which tools a model may call).

## Risks

- **Fail-open default:** a project forgetting to return a list for a scope. Mitigated by the `None` rule (scoped chat gets no tools) and the test for it.
- **Registry drift:** a driver that kept a reference to the global registry would execute a tool the chat was not offered. Covered by the per-driver refusal test.
- **Behaviour change for existing consumers:** none intended; the neutrality test is part of the required tests.
- **Overlap with `DCM-CHAT-10`:** both edit the turn runner; ordering is the mitigation.

## Required tests to write

- Hook configured, scoped chat: the provider receives exactly the list the hook returned for that scope, and a global tool is absent.
- Two chats in two scopes in one test run get their own different tool sets; one scope's tools never appear in the other.
- Hook returns `None`: an unscoped chat gets the global registry, a scoped chat gets no tools.
- Hook returns an empty list: no tools offered, for scoped and unscoped chats.
- The model requesting a tool that is not in the chat's offered set (a global tool, or another scope's tool) is refused by each driver (Claude Code CLI, OpenAI API, Anthropic API) and the handler is not called.
- A handler receives the chat, and `chat.scope` is the scope the chat was created with.
- Invalid list from the hook (duplicate or malformed name, non-dict schema) fails the turn like an invalid registry; a raising hook fails the turn with `provider_error` and no provider call happens.
- Neutrality: with the hook unset, the existing assistant tests pass unchanged, and a scoped chat still gets the global registry.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. `DCM-CHAT-10` has landed (3.1.0); this order builds on it.

## Context package (verified against the landed code, paths under `src/django_core_micha/assistant/`)

- `hooks.py`: `ToolSpec`; `_hook(name, default)` resolves `DCM_ASSISTANT_<NAME>` (dotted path or callable); `tool_registry()` reads `DCM_ASSISTANT_TOOL_REGISTRY` and validates it (names unique and matching `[A-Za-z0-9_-]{1,64}`, `input_schema` a dict, else `ValueError("invalid_tool_registry")`). Add `tools_for_chat(chat)` here: the hook is `DCM_ASSISTANT_TOOLS_FOR_CHAT(chat) -> iterable[ToolSpec] | None`. Not configured: return `tool_registry()` for every chat (today's behaviour). Configured: a returned iterable is exactly that chat's set (an empty one means no tools) and is validated with the SAME checks as the global registry (extract the validation into one helper both use); `None` means the global registry when `chat.scope` is empty and an empty list when the chat has a scope. A raising hook or an invalid list raises, and `run_turn` already turns any exception raised before the provider call into a failed turn with `provider_error` and no provider call (the tools are resolved before `_make_driver`).
- `turns.py` `run_turn`: the line `tools = await sync_to_async(tool_registry)()` (about line 146) becomes `tools = await sync_to_async(tools_for_chat)(current)`; the import list at the top (`from .hooks import ... tool_registry ...`) changes accordingly. Nothing else in the runner changes.
- Drivers already receive only the chat's `tools` per call and refuse names that are not in it: `drivers/openai_api.py` (`tool_map = {t.name: t for t in tools}`, refusal `if calls > max_tool_calls or call["name"] not in tool_map: raise DriverError("security_violation")`), `drivers/anthropic_api.py` (same), `drivers/claude_cli.py` (the bridge `handle` closure looks the requested name up in `tools` and answers `tool_not_allowed`; `cli_bridge.py` offers only the tools in its per-chat config and refuses other names; the init event must list exactly the offered tools). Do NOT change driver behaviour unless a test shows a driver can reach a tool outside its `tools` list; the work here is to prove it per driver.
- `drivers/base.py` `invoke_tool(tool, user, chat, arguments, trust_class)` already passes the chat to the handler; a test pins that `chat.scope` is the scope the chat was created with.
- Docs: `docs/design/assistant-chat.md` (describe the hook, the `None` rule, that the per-scope system prompt comes from the existing `DCM_ASSISTANT_SYSTEM_PROMPT_BUILDER(chat)`, and that handlers receive the chat). Do NOT edit `CHANGELOG.md`, `pyproject.toml`, `WORK_ORDERS.md` or `webapp-management` (the Orchestrator releases).

## Tests

New file `assistant/tests/test_tools_for_chat.py` only (do not edit existing test files). Imitate `assistant/tests/test_turn_hooks.py` (fake drivers through `run_turn`: `_run(monkeypatch, chat, user, driver, frames)` pattern, `override_settings`) and `assistant/tests/test_core.py` (`test_openai_stream_and_tool_round_trip_reserves_each_call`, `test_anthropic_native_tool_round_trip_and_cost_per_provider_call`, the `_cli_proc` fake-process helpers in `test_core.py` and `test_cli_pre_init.py`). Cover every bullet of Part A "Required tests to write", including per driver: OpenAI and Anthropic with fake clients that request a tool outside the chat's list (the handler must not be called, `DriverError` code `security_violation`); Claude Code: the bridge refuses a name that is not in the chat's offered list (test `cli_bridge` with a config file naming only the chat's tools via `DCM_ASSISTANT_BRIDGE_CONFIG`, and the init-event check with a fake process listing an extra tool). Run your tests with the interpreter from the dispatch note.

## Invariants
Unset hook = identical behaviour; a scoped chat never inherits global tools when the hook is configured and returns `None`; no driver change that weakens a refusal; tools stay read-only by contract.

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

Gate #1 open until recorded in the register row. Tier 3 · tests: the groups of Part A. Starts after `DCM-CHAT-10` has landed. Orchestrator-owned: `CHANGELOG`, version bump and publish from `main`, the `SHARED_CAPABILITIES.md` row, the review lenses (envelope, regression, duplication, tests) plus security over the diff, register row. After the release, `cockpit/CKP-REM-6` bumps its own pin.
