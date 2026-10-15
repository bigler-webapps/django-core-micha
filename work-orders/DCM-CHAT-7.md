# DCM-CHAT-7 — A provider timeout before init also discards a resumed CLI session

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-5` (released in 3.0.1), a known gap reported at its landing.

# A. Envelope — addressed to everyone

## Goal and expected outcome

`DCM-CHAT-5` made the core discard a resumed Claude Code session that fails before the CLI's `init` event, so that the next turn starts fresh from the stored history. One path escapes this. The turn runner wraps the whole driver stream in its own provider timeout (`assistant/turns.py`, `run_turn`). When that outer timeout fires, the exception it raises carries no information on whether the CLI reached `init`. The `TimeoutError` branch therefore never discards the session, and a chat whose resumed session hangs before `init` times out on every later turn.

Expected outcome: a turn on a resumed Claude Code session that ends in a provider timeout before `init` discards the session, exactly like every other pre-init failure. A timeout after `init` keeps the session as today.

## Scope

- The turn runner learns, for a timed-out turn, whether the Claude Code driver had seen `init`. On a resumed session without `init`, it clears the chat's provider session reference. The turn itself still ends as failed with `timeout`, without a retry.
- The `CHANGELOG` names the change.

## Non-goals

- Changing the timeout values or the timeout taxonomy.
- The API drivers, which keep no provider session.
- Retrying a timed-out turn.
- The pre-init event rule from `DCM-CHAT-5`.

## Tier

Tier 3: a change inside shared-core.

## Risks

- **Two timeouts:** the driver and the turn runner each enforce a provider timeout. A fix that covers only one of them leaves the gap open. The test must drive the outer one.

## Required tests to write

- A resumed session whose stream produces nothing until the turn-level provider timeout fires: the turn ends failed with `timeout`, and the chat's provider session reference is cleared.
- The same timeout after a valid `init`: the session reference is kept.
- A fresh, non-resumed session that times out before `init`: behaviour as today.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Run tests with the scratch venv interpreter named in your dispatch note, not the global python. A second work order (DCM-CHAT-8) is implemented in the same run: it owns `assistant/drivers/base.py`, `drivers/openai_api.py`, `drivers/anthropic_api.py` and `tests/test_driver_logging.py`; this one owns `assistant/turns.py`, `drivers/claude_cli.py` (only the init marker below) and `tests/test_pre_init_timeout.py`.

## Context package

- **The gap (verified):** `src/django_core_micha/assistant/turns.py`, `run_turn`: `async with asyncio.timeout(get_limit("provider_timeout")): async for event in driver.stream(...)`. When it fires, `asyncio.timeout` replaces the `CancelledError` with a plain `TimeoutError`, so a flag the driver put on the cancellation (as `DCM-CHAT-5` did, `exc.before_init`) is lost. The `except TimeoutError` branch in `run_turn` carries a dead `getattr(exc, "before_init", False)` check. The driver's own timeout (`drivers/claude_cli.py`, `async with asyncio.timeout(get_limit("provider_timeout"))`) already sets `before_init` on the `DriverError` it raises.
- **Required design (do not rely on the exception):** the turn runner must itself know whether `init` was seen. Suggested seam: the CLI driver yields one small marker event `{"type": "init"}` at the moment it accepts a valid `init` (`saw_init = True` in `read_events`); `run_turn`'s event loop ignores unknown types today, so add an `elif event.get("type") == "init": saw_init = True` branch and a local `saw_init = False` next to `session_file`. In the `except TimeoutError` branch: if `session_file` is true and `saw_init` is false, clear `session_driver` and `session_reference` (the same `AssistantChat.objects.filter(pk=chat.pk).update(...)` write used by the neighbouring branches). Replace the dead `before_init` check there. A timeout after a valid init keeps the session; a non-resumed (fresh) session that times out before init behaves as today (nothing to discard). The failed turn still ends `failed` with `timeout`, no retry. Everything else (CHAT-5 branches) stays.
- **Tests:** NEW file `src/django_core_micha/assistant/tests/test_pre_init_timeout.py`. Imitate `test_resumed_pre_init_failure_discards_reference_and_next_turn_rebuilds_history` in `assistant/tests/test_cli_pre_init.py` (fake driver run through `run_turn`, the session `.jsonl` created under `<DCM_ASSISTANT_CLI_ROOT>/<chat pk>/config/projects/project-a/old-session.jsonl` so `session_file` is true, `override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path, DCM_ASSISTANT_LIMITS={"provider_timeout": 1})`). Cases per Part A: (1) fake driver that never yields (awaits an `asyncio.Event().wait()`): turn ends failed with `timeout` and the session fields are cleared; (2) fake driver that yields `{"type": "init"}` and then hangs: failed with `timeout`, session fields kept; (3) fresh session (no `.jsonl`, reference unset or file absent) that hangs: failed with `timeout`, chat unchanged. The tests must drive the turn-level timeout, so the fake drivers must not raise their own timeout.
- **Docs:** do not edit `CHANGELOG.md`, `pyproject.toml` or `docs/` (the Orchestrator releases).

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

Gate #1 recorded at django-core-micha@afccd7e. Tier 3 · tests: the three of Part A. Implemented together with `DCM-CHAT-8` in one codex/gpt-6-luna dispatch; combined review (envelope, regression, duplication, tests, security); patch release 3.0.2 with a CHANGELOG entry; publish run observed. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19.
