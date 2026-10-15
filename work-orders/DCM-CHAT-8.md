# DCM-CHAT-8 — API driver failures are logged on the server

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found while diagnosing `cockpit/CP-CHAT-5`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The OpenAI and Anthropic drivers of the chat core turn every provider exception into `DriverError("provider_error")` (`assistant/drivers/openai_api.py`, `assistant/drivers/anthropic_api.py`). The turn runner does not log `DriverError` (`assistant/turns.py`, `run_turn`). A provider rejection, such as an HTTP 400 for an invalid tool schema, therefore leaves no trace on the server. The user sees "The answer could not be completed", and the operator has nothing to diagnose from. In `cockpit/CP-CHAT-5` the cause could only be found by reproducing the request by hand. survey_app learned the same lesson with its CLI motor: before AIB-7, stderr was discarded.

Expected outcome: every failed provider call in an API driver writes one server-side log entry. The entry holds the driver, the model, the HTTP status, the provider's error type and code, the offending parameter when the provider names one, and the provider's error message. The client keeps receiving only the generic error code.

## Scope

- **Logging:** one warning-level log entry per failed provider call in the OpenAI and Anthropic drivers, with the fields above.
- **Redaction:** before logging, any API-key-like token in the message is redacted (`sk-…` and the Anthropic key form).
- **Unchanged:**
  - the client-facing error codes and the WebSocket frames;
  - the quota mapping (429 to `quota_exceeded`);
  - the Claude Code CLI driver, which already logs its stderr tail.

## Non-goals

- Logging request or response bodies, prompts, tool content or knowledge-base text. Only the provider's error description is logged.
- Showing provider messages to the user.
- Metrics or alerting.

## Tier

Tier 3: a change inside shared-core, touching what is written to logs.

## Risks

- **Data in logs:** provider error messages can quote parts of the request, for example a parameter value. Logging only the error object, never the request, keeps that bounded. Redacting key-like tokens covers the case where a provider echoes a key prefix.

## Required tests to write

- An OpenAI `APIStatusError`-like failure with status 400 and an error body writes one log entry containing status, type, code, param and message. The client still receives `provider_error`.
- The same for the Anthropic driver.
- A key-like token inside the provider message appears redacted in the log entry.
- A 429 still maps to `quota_exceeded`.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Run tests with the scratch venv interpreter named in your dispatch note, not the global python. A second work order (DCM-CHAT-7) is implemented in the same run: it owns `assistant/turns.py`, `assistant/drivers/claude_cli.py` and `tests/test_pre_init_timeout.py`; this one owns `assistant/drivers/base.py`, `assistant/drivers/openai_api.py`, `assistant/drivers/anthropic_api.py` and `tests/test_driver_logging.py`. Do not touch the other files.

## Context package

- **Seams (verified):** `src/django_core_micha/assistant/drivers/openai_api.py` (outer handler near line 75-79: `except DriverError: raise` then `except Exception as exc:` which maps `status_code == 429` to `quota_exceeded`, everything else to `provider_error`, `raise DriverError(code) from exc`) and `src/django_core_micha/assistant/drivers/anthropic_api.py` (same shape near line 67-71). Neither logs. Shared helpers live in `drivers/base.py` (`DriverError`, `invoke_tool`, `_reserve`, `map_effort`).
- **Required change:** one shared helper in `drivers/base.py` (for example `log_provider_error(driver, model, exc)`), called from the two `except Exception` handlers before the `DriverError` is raised. It writes ONE `logging` warning entry (a module logger `logging.getLogger(__name__)` in `base.py`) with: driver name (`openai_api` / `anthropic_api`), model, HTTP status (`getattr(exc, "status_code", None)`), the provider's error type, code and param, and the provider's error message. Read these defensively from the exception: openai `APIStatusError` exposes `.status_code`, `.body` (a dict with `message`, `type`, `code`, `param`), `.code`, `.param`, `.type` on some subclasses and `.message`; anthropic `APIStatusError` exposes `.status_code` and `.body` shaped like `{"type": "error", "error": {"type": ..., "message": ...}}`. Handle `body` being a dict, a string or None, and a missing attribute; never raise from the logging helper (wrap its own work so logging can never mask the original error); for a non-HTTP exception (connection error, timeout) log the exception class name and message with status None.
- **Redaction:** before logging, replace API-key-like tokens in the message (and any other logged string) with a fixed marker: `sk-` followed by 8 or more key characters (this also covers the Anthropic form `sk-ant-...`), using one compiled regex. Log only the error object's fields, never request bodies, prompts, tool content or messages lists.
- **Unchanged:** client-facing error codes and frames; the 429 mapping to `quota_exceeded` (the log entry is still written for a 429); the cost-guard release/settle paths; the CLI driver (it already logs stderr).
- **Tests:** NEW file `src/django_core_micha/assistant/tests/test_driver_logging.py`. Use the existing API-driver test style in `assistant/tests/test_core.py` (`test_openai_stream_and_tool_round_trip_reserves_each_call`, `test_anthropic_native_tool_round_trip_and_cost_per_provider_call`: fake clients built with `SimpleNamespace`, `monkeypatch` of `_reserve`/`settle_reservation`/`release_reservation`, `async_to_sync` to run the driver generator) and pytest's `caplog` (`caplog.at_level(logging.WARNING, logger="django_core_micha.assistant.drivers.base")` or the logger name you choose). Cases per Part A: (1) OpenAI: the fake client's `responses.create` raises an exception object with `status_code=400` and a `body` dict (`message`, `type`, `code`, `param`): exactly one log record whose text contains status 400, the type, the code, the param and the message, and the driver still raises `DriverError` with code `provider_error`; (2) the same for the Anthropic driver (body shape above); (3) a key-like token such as `sk-proj-abcdefgh12345678` in the provider message does not appear in the log text and the redaction marker does; (4) a 429 still raises `DriverError("quota_exceeded")`.
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

Gate #1 recorded at django-core-micha@afccd7e. Tier 3 · tests: the four of Part A. Implemented together with `DCM-CHAT-7` in one codex/gpt-6-luna dispatch; combined review (envelope, regression, duplication, tests, security); patch release 3.0.2 with a CHANGELOG entry; publish run observed. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19.
