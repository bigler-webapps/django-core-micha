# DCM-CHAT-4 — Chat core accepts synchronous tool handlers

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found live on staging in `cockpit/CP-CHAT-4`. Released together with `DCM-CHAT-5`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The chat core calls each registered tool handler directly inside its asyncio event loop (`assistant/drivers/base.py`, `invoke_tool`). A plain synchronous handler, which is the normal case in a Django app, therefore runs on the event loop. A handler that reads from the database fails there with `SynchronousOnlyOperation`, and the core reports that as a provider error. The design document describes the handler as `handler(user, chat, arguments, trust_class) -> (content, sources)` and does not say it has to be async. cockpit hit this and wrapped its handler in an async function (cockpit `7ddce49`).

Expected outcome: a tool handler may be either synchronous or async. The core runs a synchronous handler in a worker thread, where database access is allowed. It awaits an async handler as it does today. The design document states both forms.

## Scope

- **Handler invocation:** synchronous handlers run off the event loop in a worker thread that permits Django ORM access. Async handlers are awaited unchanged. Every other part of `invoke_tool` stays as it is: argument validation, the result shape check, the prompt-size bound and the error mapping.
- **Documentation:** `docs/design/assistant-chat.md` and the `CHANGELOG` describe both handler forms. They also state that a synchronous handler may use the ORM.

## Non-goals

- Changing the handler signature, the result shape or the tool registry setting.
- Any change in cockpit. Its async wrapper keeps working and is not removed here.
- Every other driver or turn behaviour (pre-init events and session recovery are `DCM-CHAT-5`).

## Tier

Tier 3: a change inside shared-core.

## Risks

- **Thread choice:** a worker thread that is not allowed to use the ORM would move the failure without fixing it. The test must perform a real database read inside a synchronous handler.
- **Double invocation:** a handler must still be called exactly once per tool call.

## Required tests to write

- A synchronous handler that performs a database read returns its content and sources through `invoke_tool`.
- An async handler still works unchanged.
- An exception raised inside a synchronous handler maps to the same error as today.
- A synchronous handler is called exactly once per tool call.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Run tests with the scratch venv interpreter named in the environment note of your dispatch, not the global python.

## Context package

- **Seam:** `src/django_core_micha/assistant/drivers/base.py`, `async def invoke_tool(tool, user, chat, arguments, trust_class)` (about line 61): it calls `tool.handler(user, chat, arguments, trust_class)`, awaits the result if `asyncio.iscoroutine(result)`, validates the `(content, sources)` shape, applies the `max_prompt_bytes` bound and maps any exception to `DriverError("provider_error")`. The handler call is currently made directly on the event loop, which is why a synchronous ORM read raises `SynchronousOnlyOperation`.
- **Required change:** a synchronous handler runs in a worker thread that permits ORM access (the project already depends on `asgiref`; `asgiref.sync.sync_to_async` with its default thread-sensitive behaviour is the Django-sanctioned way), an async handler (a coroutine function, or a callable returning an awaitable) is awaited as today. Decide sync versus async by inspecting the handler, so a handler is called exactly once. Keep everything after the call unchanged.
- **Callers:** `drivers/claude_cli.py` (the bridge `handle` coroutine calls `invoke_tool`), `drivers/openai_api.py` and `drivers/anthropic_api.py`. They call `invoke_tool` and need no change.
- **Docs:** `docs/design/assistant-chat.md` (the paragraph describing the tool handler / registry hook) must state that a handler may be sync or async and that a sync handler may use the ORM. Do NOT edit `CHANGELOG.md` or `pyproject.toml` (the Orchestrator releases).
- **Existing tests to imitate:** `src/django_core_micha/assistant/tests/test_core.py` has `test_registered_tool_handler_gets_trust_class` and the API-driver round-trip tests (handlers there are plain lambdas, i.e. synchronous: they must keep working). New tests go in a NEW file `src/django_core_micha/assistant/tests/test_tool_handlers.py`. The real database read test needs `@pytest.mark.django_db(transaction=True)` (a worker thread cannot see an uncommitted test transaction) and a model that exists in the test settings, for example the auth user model.
- **Invariants:** result-shape check, prompt-size bound and error mapping stay exactly as they are; no handler is called twice; exceptions from a sync handler map to the same `DriverError("provider_error")` as today.

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

Gate #1 recorded at django-core-micha@8023f1d. Tier 3 · tests: the four of Part A. Implemented together with `DCM-CHAT-5` in one dispatch (codex/gpt-6-luna), one patch release, review lenses envelope, regression, duplication, tests plus `sec_reviewer` over the combined diff, register rows for both, publish run observed. Live check on cockpit staging is the CHAT-5 step below. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19.
