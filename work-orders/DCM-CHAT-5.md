# DCM-CHAT-5 — A chat survives an interrupted Claude Code turn

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found live on staging in `cockpit/CP-CHAT-4`. Released together with `DCM-CHAT-4`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The Claude Code CLI driver treats every event before the CLI's `init` event as a security violation (`assistant/drivers/claude_cli.py`, the `elif not saw_init` branch). After a turn is interrupted, the CLI (verified with version 2.1.292) emits `{"type":"system","subtype":"ui_invalidate"}` before `init` when the session is resumed. Three things interrupt a turn: the user pressing Stop, a dropped socket, or a backend restart. Every later turn in that chat then fails with `security_violation`. The chat is permanently unusable, while a new chat works. Stop is a primary control of the chat surface, so this hits ordinary use.

Expected outcome: a chat keeps working after an interrupted turn. The check that the CLI offers exactly the registered tools stays as strict as it is. A chat whose resumed provider session fails before `init` for any other reason recovers on its next turn, from its stored history.

## Operator decisions this Envelope records (2026-10-15)

- Tolerate benign pre-init system events **and** add self-healing for resumed sessions.
- Released together with `DCM-CHAT-4` as one patch.

## Scope

- **Events before `init`:**
  - An event with `type` `system` and any subtype other than `init` is ignored. It is not forwarded and not treated as an error.
  - Any event of another type before `init` still ends the turn with `security_violation`.
  - The `init` check itself (exact tool list, exact MCP server set) is unchanged.
  - Behaviour after `init` is unchanged.
- **Self-healing:** a turn that resumed an existing provider session and fails before the `init` event, with any error, makes the core discard the chat's provider session reference.
  - The next turn in that chat then starts a fresh provider session, with the history rebuilt from the stored turns. This is the same path a driver switch already takes.
  - The failed turn itself still ends as failed with its error. There is no automatic retry inside the same turn.
- **Documentation:** the design document and the `CHANGELOG` describe both behaviours.

## Non-goals

- Loosening the `init` check, or accepting any non-system event before it.
- Retrying a failed turn automatically.
- Cleaning up old session files on disk.
- The OpenAI and Anthropic API drivers.
- Changes in cockpit, apart from the later pin bump that `CP-CHAT-4` does itself.

## Tier

Tier 3: a change inside shared-core that touches a security check.

## Risks

- **Weakening the security check:** ignoring events before `init` must not let model output or tool activity through unchecked. Only `system` events are ignored, and nothing reaches the client before a valid `init`.
- **A genuine security violation on resume:** if the init check fails because the tools really differ, discarding the session only moves the failure to a fresh session, which runs the same check. Nothing is gained by bypassing it, and nothing is lost.
- **CLI drift:** later CLI versions may emit other system subtypes before `init`. The rule covers them by type, not by a list of subtypes.

## Required tests to write

- A stream with `{"type":"system","subtype":"ui_invalidate"}` before a valid `init` completes the turn normally.
- A non-system event (for example an `assistant` event) before `init` still ends with `security_violation`.
- A wrong `init` (extra tool) still ends with `security_violation`, also when a system event came first.
- A resumed session failing before `init`:
  - clears the chat's provider session reference;
  - the next turn starts without resume and with the history rebuilt from the stored turns.
- A fresh, non-resumed session failing before `init` leaves the chat as it is today, because there is nothing to discard.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Run tests with the scratch venv interpreter named in the environment note of your dispatch, not the global python.

## Context package

- **Pre-init handling:** `src/django_core_micha/assistant/drivers/claude_cli.py`, the `read_events` loop. Today: `if event.get("type") == "system" and event.get("subtype") == "init": ... saw_init = True` then `elif not saw_init: raise DriverError("security_violation")`. Required: before `init`, an event with `type == "system"` and any subtype other than `init` is skipped (`continue` semantics: not yielded, not an error, but still counted toward the output byte total); any other pre-init event keeps raising `security_violation`; the `init` check (exact tool list, exact MCP server set, duplicates and non-dict server entries already handled) and everything after `init` stay as they are. Note the loop drains `tool_events` before the if-chain; keep that working.
- **Self-healing signal:** the driver must tell the turn runner that it failed before `init`. Suggested seam: any `DriverError` (and the generic exception mapped to `provider_error`, and timeouts) that leaves `ClaudeCliDriver.stream` while `saw_init` is still False gets an attribute `before_init = True` (default False on `DriverError` in `drivers/base.py`, which already takes an optional message). Do not change error codes.
- **Turn runner:** `src/django_core_micha/assistant/turns.py`, `run_turn`. It computes `session_file` (true when the chat's `session_driver` equals the turn driver `claude_cli`, a `session_reference` exists and its `.jsonl` is found under the chat's config dir) and passes `session_id=current.session_reference if session_file else None`. In the `except` handling of `run_turn` (the `except Exception as exc` branch that calls `_finish(... "failed" ...)`), when the failure is a `DriverError` with `before_init` set AND the turn resumed an existing session (`session_file` true), clear the chat's `session_driver` and `session_reference` (a small `AssistantChat.objects.filter(pk=...).update(...)` through `sync_to_async`, like the neighbouring writes). The failed turn still ends as failed with its error code and is still re-raised; no retry. A non-resumed session failing before init changes nothing. The next turn then takes the existing path: no `session_file` -> no resume, prompt built from `build_history` (see `history.py`) plus an `history_rebuilt` notice when a reference was set before.
- **Existing tests to imitate:** `assistant/tests/test_core.py` has `_cli_proc(rows, stdout_hook=None)` (fake process with scripted stdout lines), `test_cli_reads_assistant_text_from_message_content_and_reports_session`, `test_cli_duplicate_tool_in_init_event_is_a_security_violation...`, `test_cli_non_dict_server_entry_is_a_security_violation`, `_run_turn_with_driver` and `test_history_rebuild_happens_after_driver_switch_through_a_turn` (a turn run through `run_turn` with a fake driver). Put the new tests in a NEW file `src/django_core_micha/assistant/tests/test_cli_pre_init.py` (import the helpers by copying the few lines you need; do not edit `test_core.py`). The fake-process tests need `monkeypatch.setattr(cli.shutil, "which", lambda name: "claude")` and `override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path)`. For the resume test the session `.jsonl` must exist under the chat's config dir (see `_session_dirs` and the glob in `run_turn`).
- **Docs:** `docs/design/assistant-chat.md` (lifecycle and provider security sections) must describe both behaviours: pre-init `system` events are ignored, any other pre-init event and a wrong `init` still fail closed; a resumed session that fails before `init` is discarded so the next turn rebuilds from stored history. Do NOT edit `CHANGELOG.md` or `pyproject.toml`.
- **Invariants:** nothing reaches the client before a valid `init`; the init check is not loosened; behaviour after `init` is unchanged; the OpenAI and Anthropic drivers are untouched.

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

Gate #1 recorded at django-core-micha@8023f1d. Tier 3 (security check) · tests: the five of Part A. Implemented together with `DCM-CHAT-4` in one dispatch (codex/gpt-6-luna), one patch release 3.0.1, review lenses envelope, regression, duplication, tests plus `sec_reviewer` over the combined diff. Live check after publishing, on cockpit staging through `CP-CHAT-4`: stop a running Claude Code turn, then send a further message in the same chat; it must answer. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19.
