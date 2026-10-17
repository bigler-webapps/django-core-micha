# DCM-CHAT-14 — Aborted assistant turns are charged, not released (closes S434)

# A. Envelope

**Goal.** Close S434 from the 2026-10-17 estate audit (`webapp-management/SECURITY_FINDINGS.md`). Expected
outcome: stopping or timing out an assistant turn after the provider call has started no longer frees the
cost reservation; the daily cap counts the turn and the cost overview shows it.

**Background (from the finding).** In the paid API drivers (`assistant/drivers/anthropic_api.py`,
`openai_api.py`, base in `drivers/base.py`) `except BaseException: release_reservation(...)` also catches the
task cancellation from a `stop` frame (`assistant/consumers.py`) and provider timeouts. Usage is set only
after the final message, so an aborted turn writes no `AICostRecord` while the partial answer is still
stored. The extraction side already keeps ambiguous reservations until they expire. A user sends a large
prompt, reads the deltas and stops before the end: every turn is admitted against the cap and then
released, so the daily counter never grows.

**Scope.**
1. A reservation is released only when the provider call has not started. Once it has started, an aborted
   turn (stop, cancellation, timeout, provider error) is charged conservatively (measured usage where known,
   otherwise the reserved amount) and written as an `AICostRecord`, consistent with how extraction treats
   ambiguous calls.
2. The reservation uses the turn's real `max_tokens`.
3. Minor release with a CHANGELOG entry.

**Non-goals / do not touch.** The caps and limits themselves; frame and concurrency limits; extraction; app
pin bumps (separate).

**Risks.** Charging users for genuine provider failures after the call started (accepted: conservative);
turns that fail before anything is sent must stay free.

**Required tests (write as part of the implementation).**
- `stop` after the provider call started → reservation not released, cost record written, cap counter
  increased.
- Failure before the provider call → released, no record.
- Timeout mid-stream → charged.
- Normal completion → actual usage as today.
- The reservation amount follows the turn's `max_tokens`.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (shared core, cost control).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, the exact rule for "started", and the pitfalls of charging inside a cancelled task. One dispatch; complete, no stubs. This is cost control in shared core: a turn that was admitted and reached the provider must never be free.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's), `extraction/*`, `billing/*`, `assistant/consumers.py`, `assistant/turns.py`. Run tests with the interpreter named in your dispatch note.

### Context package (paths under `src/django_core_micha/assistant/`)

- `drivers/base.py`: `_reserve(user_id, model, prompt_bytes, output_tokens=2048)` estimates `input_tokens = max(1, (prompt_bytes + 1) // 2)`, prices it with `extraction.cost_guard.estimate_cost_cents`, calls `reserve_user_budget(user_id=, cents=)` and turns `AICostLimitExceeded` into `DriverError("quota_exceeded")`; it returns a reservation object (`None` when accounting is disabled or the user is anonymous). `_settle(reservation, model, usage)` settles with measured usage through `settle_reservation`. `call_with_budget(...)` (used by the completion path) reserves, runs the operation, settles on a final response and does `except BaseException: release_reservation(...)`. `Usage`, `DriverError`, `log_provider_error` live here too.
- `drivers/anthropic_api.py` `AnthropicDriver.stream(...)`: per round, `reservation = _reserve(user pk, model, json prompt bytes + system bytes)` (default 2048 output tokens although the request sends `max_tokens=8192`), then `stream_context = await create()` (`self.client.messages.stream(model=, system=, messages=, max_tokens=8192, tools=, output_config=)`), `async with stream_context as stream`, text deltas yielded, `msg = await stream.get_final_message()`, `settle_reservation(reservation, response=msg, model)`; `except BaseException: release_reservation(...); raise`. This is S434: cancellation (a `stop` frame cancels the turn task in `consumers.py`), a provider timeout or an error mid-stream all release the reservation, and no cost record exists because usage is only produced after the final message.
- `drivers/openai_api.py` `OpenAIDriver.stream(...)`: per round `_reserve(...)` (default 2048 output tokens; the request has no output cap), `stream = await self.client.responses.create(model=, input=, stream=True, tools=, reasoning={"effort": ...})`, event loop yielding deltas, `response.completed` carries `usage`, `settle_reservation(...)` on success, `except BaseException: release_reservation(...)`; the usage event is yielded after settling.
- Where a cost record comes from: `assistant/turns.py::_finish` writes an `AICostRecord` through `billing.services.record_ai_call(user=, provider=, model=, input_tokens=, output_tokens=, cache_read_tokens=, cache_write_tokens=, purpose="chat", reference=str(turn.pk))` only when the turn finished with a non-empty `usage` and the `django_core_micha.billing` app is installed; `assistant/completion.py` does the same for completions. An aborted turn has empty usage, so today it writes nothing. `billing.services.record_ai_call` is synchronous (database write).
- The cost guard (`extraction/cost_guard.py`, do not edit): `reserve_user_budget`, `settle_reservation(reservation=, response=, model=)` (adds the actual cents to the day counter, then drops the reservation), `release_reservation(reservation=)`, `estimate_cost_cents`, `_response_cost_cents`. `extraction/service.py` is the model for ambiguous failures: it releases only when the provider request never started or the error is a definite client error (`_definitively_unbilled`: not started, or HTTP status 400-499 except 408, 409, 429); everything else keeps the cost. The cache in the tests is Django's local memory cache, and `AI_DAILY_COST_LIMIT_CENTS` defaults to 500; enabling/disabling and TTLs are in `cost_guard._is_enabled()`.
- Existing driver tests to model on: `tests/test_usage_cost_chunk_a.py`, `tests/test_core.py`, `tests/test_driver_logging.py` (look at how they fake the provider clients and drive `stream()`).

### Fixed design

1. **Real output size in the reservation.** `_reserve` keeps its signature but both API drivers pass the real output ceiling of the round: a module constant in `drivers/base.py` `DEFAULT_MAX_OUTPUT_TOKENS = 8192` (the value `anthropic_api.py` sends today as `max_tokens`) used by the Anthropic driver BOTH for the request's `max_tokens` and for the reservation, via an optional `max_tokens` keyword of `stream()` (default the constant). For OpenAI the request stays uncapped as today (do not add `max_output_tokens`, that would change provider behaviour), but the reservation uses the same `max_tokens` value so it is no longer the 2048 guess; document that in a comment.
2. **"Started" and the single exit rule.** Each round tracks `started = False` and sets it to True immediately before the awaited SDK call (`self.client.messages.stream(...)`/`async with` entry for Anthropic, `await self.client.responses.create(...)` for OpenAI), i.e. the moment bytes may reach the provider. A new helper pair in `drivers/base.py` implements the rule once for both drivers: `release_unstarted(reservation)` and an async `charge_aborted(reservation, *, user, provider, model, input_tokens_estimate, output_tokens_estimate, reference, exc)`. In the existing `except BaseException` of both drivers: `if not started` -> `release_reservation(...)` exactly as today and re-raise; if started and the exception is a definite client error (an SDK error whose `status_code` is an int 400-499 other than 408, 409, 429, read with `getattr`) -> release (the provider did not bill it) and re-raise; otherwise (cancellation `asyncio.CancelledError`, generator close `GeneratorExit`, timeouts, connection errors, 5xx, 408/409/429, any other exception) -> `charge_aborted(...)` and re-raise the original exception unchanged. Cancellation must still propagate: re-raise after the charge and never swallow `CancelledError`.
3. **What an aborted turn is charged.** Measured usage is not available on an abort mid-stream, so the charge is the RESERVED amount, conservatively: settle the reservation with a response whose usage equals the reservation's own token estimate (the same `input_tokens` the reservation priced, `output_tokens` = the round's `max_tokens`), via `settle_reservation(reservation=, response=Usage-like, model=)`, so the day counter grows by the reserved cents and the reservation is dropped (no double counting and no leak). Use the same estimate for the record. The estimate values come out of `_reserve`'s own computation (extend `_reserve` or its return so the driver can hold `(reservation, input_tokens_estimate, output_tokens_estimate)`; keep `call_with_budget` working unchanged for its other users).
4. **Cost record for the aborted round.** After the cache charge, write ONE `AICostRecord` for the aborted round when `django_core_micha.billing` is installed (`apps.is_installed`), through `billing.services.record_ai_call(user=, provider=<"anthropic_api"|"openai_api">, model=, input_tokens=<estimate>, output_tokens=<estimate>, purpose="chat_aborted", reference=<chat pk as string when a chat is passed, else "">)`. The call is synchronous: run it with `asgiref.sync_to_async(..., thread_sensitive=True)` and shield it from the cancellation that is already in flight (`asyncio.shield`) so a `stop` frame cannot cancel the write; any exception from it is logged by class name only and never replaces the original exception. The rounds of a turn that finished normally keep their `chat` record from `_finish` (purpose `chat`); an aborted round is recorded by the driver (purpose `chat_aborted`) and the turn's own `usage` stays empty, so nothing is counted twice. When billing is not installed only the cache charge happens.
5. **Unchanged:** the caps and their settings, frame and concurrency limits, extraction, `consumers.py`/`turns.py`, normal completion (settle with the actual usage as today), a failure before the call (released, no record), the completion path `call_with_budget` (leave it as it is unless it needs the same shared helper; do not change its behaviour), `claude_cli` (no cost guard).

### Tests (new file `tests/test_aborted_turn_cost.py`; existing tests stay green)

Follow the driver-testing pattern of the existing tests. Per driver (Anthropic and OpenAI) — the fake provider client must hand out REAL SDK objects wherever the code reads them (use `anthropic.types.Message`/`Usage` and `openai.types.responses` event/usage classes, built with their `construct`/model constructors, not dicts or bare namespaces; at least the normal-completion case per driver uses them): (a) the task is cancelled after the first delta (use `asyncio.create_task` on a consumer of `stream()` and cancel it): the reservation is NOT released (the day counter grew by the reserved cents and the live reservation is gone, i.e. spend counted once), an `AICostRecord` with purpose `chat_aborted` exists with the estimated tokens and the user, and the `CancelledError` propagates; (b) the consumer simply stops iterating (async generator closed): charged the same way; (c) a timeout (`asyncio.TimeoutError`) raised mid-stream and a connection error: charged; (d) a failure before the provider call started (for example the client raising before `started`, or the budget refusing): released, no record, counter unchanged, for `quota_exceeded` the cap error is raised as today; (e) a definite client error from the SDK call (`status_code=400`) after `started`: released, no record; a 429 and a 500: charged; (f) normal completion: settled with the actual usage exactly as before, one usage event, no `chat_aborted` record; (g) the reservation amount follows `max_tokens`: with `max_tokens=100` versus the default the reserved cents differ (capture the cents handed to `reserve_user_budget` by patching it with a recorder that calls the original) and the Anthropic request really sends that `max_tokens`; (h) the cap counter increased: after N aborted turns against a small `AI_DAILY_COST_LIMIT_CENTS` the next turn is refused with `quota_exceeded` (the S434 scenario: stop early every time must still exhaust the cap); (i) with billing not installed (use the repository's test-settings switch or patch `apps.is_installed`) only the cache charge happens and nothing raises; (j) a failing `record_ai_call` does not replace the original exception. Prove each case by reading the cache counter and the `AICostRecord` table, not by asserting a mock was called.

### Invariants

A reservation is released only when the provider call did not start or the provider definitively rejected it as a client error; every other abort is charged (reserved amount) and recorded once; cancellation and every original exception propagate unchanged; nothing is double counted between the aborted round and the turn's own usage; the reservation uses the real output ceiling; no cap, limit or other behaviour changes.

### Preamble — REQUIRED block

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

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S434; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
