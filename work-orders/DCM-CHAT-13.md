# DCM-CHAT-13 — Chat: record usage and cost of every reply, summarise long chats

## Part A — Envelope (everyone)

**Tier:** 3 — shared core, what is sent to AI providers and what it costs. **Repository:** `bigler-webapps/django-core-micha` (canonical; the ui-core-micha side is UCM-CHAT-6). **Consumers:** Maana first.

### Goal and expected outcome

A reply in a long coding chat carried about 176,000 tokens of context (measured on Maana production, 2026-10-16), and replies through the Claude subscription driver record no usage at all. With paid AI tokens, every reply must know its consumption, and long chats must not carry their whole history.

Expected outcome: every reply stores its tokens and cost; long chats are summarised so a typical reply stays at about 20,000–40,000 tokens of context; the person sees the summary and the cost of each reply.

### Settled decisions (operator, 2026-10-16)

1. **Usage for every driver:** each reply stores input, output, cache-write and cache-read tokens and the cost in USD, including the `claude_cli` driver. The cost table carries per model separate rates for input, output, cache read and cache write, and size-dependent rates where a provider has them (e.g. Haiku 5.5 above 100K prompt tokens). Operator-supplied rates 2026-10-16, USD per million tokens (input / output / cache read / cache write): Fable 5.1 10 / 50 / 0.25 / 12.50; Opus 5.5 4 / 20 / 0.20 / 5; Sonnet 5.5 2 / 10 / 0.10 / 2.50; Haiku 5.5 ≤100K 0.10 / 0.50 / 0.01 / 0.125, >100K 0.50 / 2.50 / 0.05 / 0.625; GPT-6 Astra 10 / 50; GPT-6.1 Sol 2 / 10 (cached input 0.10 per OpenAI's announcement); GPT-6 Luna 0.10 / 0.50.
2. **Summary:** above a context limit (setting, default 40,000 tokens) the older part of the chat is summarised; the most recent turns stay verbatim. The summary keeps what matters for the work: decisions, agreed codes and definitions, open questions, which material was worked on. It is shown in the chat as „Zusammenfassung bis hier" (expandable), and replaces the older turns in what is sent to the provider.
3. **Current state from lookups:** the summary does not restate the project state; the app's lookup tools provide it fresh.
4. **Billing hook:** after each reply the core calls an app hook with the recorded usage, so an app can debit tokens (DCM-BILL-1); without the hook nothing changes.
5. **Visible per reply:** the reply carries its usage for display (UCM-CHAT-6).
6. **Default:** a new chat starts with a model of the level „Ausgewogen" (operator 2026-10-16; app setting).
7. **Picker data:** the model catalogue offered to a person contains only entries usable for that account and app (provider configured, account allowed, subscription driver only for the operator's own account); each entry carries its plain name, its provider, its level (top / best / balanced / fast; operator setting per model: Fable and Astra top, Opus best, Sonnet and Sol balanced, Haiku and Luna fast; all configured models are offered), its supported efforts, and an estimate range per effort for the current chat length in the app's token unit (or a relative cost level 1–3 when the app has no tokens). The estimate uses the cost table and the chat's current context size.

### Scope and acceptance

Usage recording for all drivers, summarisation with the limit setting, the hook, the payload fields; existing chats keep working.

### Non-goals and do-not-touch

Token prices and debiting (apps), model choice, grants and tool semantics, per-user API keys.

### Risks

A summary that drops a decision changes the assistant's behaviour silently; the summary must be visible and the verbatim tail long enough. Summarising itself costs tokens and must be recorded too.

### Required tests to write or update

Usage stored for each driver (provider mocked), cost computed from the table, summary triggered at the limit and sent instead of older turns, verbatim tail kept, summary visible in history, hook called with usage, behaviour unchanged without hook.

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and seams so that UCM-CHAT-6 (UI) and MAANA-APP-8 build against them. Two chunks (two dispatches, A then B, same working tree); each chunk must be complete: no stubs, no skeleton, every bullet implemented and tested. Everything is neutral for apps that configure none of it.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories (maana and survey_app are READ-ONLY references at `..\maana\backend\assistant\` and `..\survey_app\backend\surveys\services\assistant.py`). Run tests with the interpreter named in your dispatch note.

### Context package (verified against landed 3.4.0, paths under `src/django_core_micha/`)

- Usage today: `assistant/drivers/openai_api.py` yields `{"type":"usage","usage":{"input_tokens","output_tokens"}}` (from `response.usage`), `anthropic_api.py` the same from `msg.usage`, `claude_cli.py` reads `event["usage"]` of the `result` event and keeps only `input_tokens`/`output_tokens`. Operators report that replies through `claude_cli` record no usage: read how maana (`..\maana\backend\assistant\runner.py`) and survey_app parse the stream-json `result` event (fields `usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `total_cost_usd`, `modelUsage`) and fix the mapping; the Claude Code `assistant` events also carry `message.usage` per call. `assistant/turns.py` `run_turn` aggregates `usage` over rounds (`usage[key] += value`), `_finish(...)` stores `AssistantTurn.usage` (JSON) and calls the billing hook `billing.services.record_ai_call(..., cache_tokens=...)` inside its transaction; `turn_payload(turn, chat)` returns `usage` and `billing_source`.
- Prices today: `extraction/cost_guard.py` has `_DEFAULT_MODEL_COSTS_CENTS_PER_1K` (input/output only), `get_cost_table()`, `estimate_cost_cents()`; `billing/services.py` `record_ai_call` converts that table to micro-USD (`tokens * rate * 10`).
- Catalogue today: `assistant/catalogue.py` (`entries()`, `default_entry()`, `entry_id`, `selection_of`, `entry_by_id`, `validate`, `validate_catalogue` requires exactly one `default`), `views.py` `CatalogueView` (returns `{"default": selection, "entries":[{id, driver, model, label, efforts, billing, default}]}`) and chat create/patch validation through `validate`. `availability.availability()` returns `{"status","providers":{driver: "available"|"not_configured"}}`.
- History: `assistant/history.py` `build_history(chat)` (two rows per completed turn), `turns.py` builds the CLI `prompt_data` JSON (keys `system`, `history`, `message`, optional `context`) and the API `messages`; `AssistantChat.session_tokens` is the summed input of a resumed CLI session; limits live in `assistant/limits.py` (`DEFAULTS`, `get_limit`, override `DCM_ASSISTANT_LIMITS`); one-shot calls: `assistant/completion.py` `acomplete(user, prompt=, system=, entry=|driver=,model=, effort=)`.

### Fixed design

**Chunk A — usage, prices, cost per reply, hook, payload**
1. Normalised usage, everywhere (`turn.usage`, frames, hook, billing): integers `input_tokens` (NON-cached input), `output_tokens`, `cache_read_tokens`, `cache_write_tokens`; the context of one call is their sum without output. OpenAI: `input_tokens - input_tokens_details.cached_tokens` as input, `cached_tokens` as cache read, no cache write. Anthropic and Claude Code: `input_tokens` is already non-cached, `cache_read_input_tokens` and `cache_creation_input_tokens` map to read and write. Missing detail objects mean 0. The legacy key `cache_tokens` is no longer produced by the chat drivers (the extraction path and `record_ai_call(cache_tokens=)` keep accepting it as read+write sum for compatibility).
2. Price table, one new module `django_core_micha/ai_prices.py`: `price_usage(model, *, input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0) -> int` returning micro-USD (integer, rounded half up), from the setting `AI_MODEL_PRICES_USD_PER_MILLION` (replaces the default table as a whole, like the legacy setting) with per model `{"input","output","cache_read","cache_write"}` USD per million tokens and an optional ordered `"tiers": [{"above_prompt_tokens": 100000, "input":..,"output":..,"cache_read":..,"cache_write":..}]` selected by the call's prompt tokens (input + cache read + cache write; the highest tier whose threshold is exceeded). Default table (operator, 2026-10-16, USD per million, input / output / cache read / cache write): `claude-fable-5-1` 10 / 50 / 0.25 / 12.50; `claude-opus-5-5` 4 / 20 / 0.20 / 5; `claude-sonnet-5-5` 2 / 10 / 0.10 / 2.50; `claude-haiku-5-5` 0.10 / 0.50 / 0.01 / 0.125 with the tier above 100000 prompt tokens 0.50 / 2.50 / 0.05 / 0.625; `gpt-6-astra` 10 / 50 (cache read = input, no write rate); `gpt-6.1-sol` 2 / 10 with cache read 0.10; `gpt-6-luna` 0.10 / 0.50 (cache read = input). A missing cache rate falls back to the input rate; a model absent from the new table falls back to the legacy cents-per-1k table (cents per 1k * 10 = USD per million; cache at the input rate), then to the legacy fallback rates, so no existing model loses a price. Export the table getter for the catalogue estimate.
3. Cost per reply: add `AssistantTurn.cost_usd_micros` (BigIntegerField, default 0) and `AssistantTurn.context_tokens` (PositiveIntegerField, default 0, the prompt tokens of the LAST provider call of the turn) in the models and a hand-written `assistant/migrations/0004_turn_cost_context.py` (the Orchestrator verifies with `makemigrations --check`). `_finish` computes the cost with `price_usage` from the aggregated usage of ALL rounds (tool rounds and repair rounds included) and stores it; the chat drivers' usage events are summed per round as today. `billing/services.py` `record_ai_call` gains `cache_read_tokens=0, cache_write_tokens=0` and prices with `price_usage` (keep `cache_tokens` as a stored sum read+write on `AICostRecord`), `assistant/turns.py` passes the new fields, `assistant/completion.py` and `extraction/service.py` keep working (their usage may lack the new keys: treat as 0).
4. Hook `DCM_ASSISTANT_USAGE_HOOK(chat, user, turn, usage)` (optional, `hooks.py` `_hook` style, called through `sync_to_async`): after the turn is finished (any state that reported usage), with `usage` = `{"kind": "reply"|"summary", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd_micros", "context_tokens"}`. A raising hook is logged (exception class name only) and never changes the turn. Called outside `_finish`'s transaction.
5. Payload: `turn_payload` `usage` carries all four counts plus `cost_usd_micros` and `context_tokens` (also in the final `turn` frame and the REST turn list); owner-only payloads otherwise unchanged. Document the field list in `docs/design/assistant-chat.md`.

**Chunk B — summaries, levels and picker data**
6. Limits `context_limit_tokens` (default 40000) and `summary_keep_turns` (default 6) in `assistant/limits.py`. New model `AssistantSummary` (chat FK related_name `summaries`, `up_to_sequence`, `text`, `usage` JSON, `cost_usd_micros`, `created_at`; migration `0005_assistant_summary.py`, hand-written). Audit registration like the other assistant models (redact `text`).
7. Summarisation in `run_turn`, before the prompt is built: context estimate = the last finished turn's `context_tokens` when known, else a UTF-8 byte estimate (bytes / 4) of the summary plus the verbatim turns plus the new message. When the estimate exceeds `context_limit_tokens` and more than `summary_keep_turns` completed turns follow the latest summary, summarise the oldest of those (all but the last `summary_keep_turns`) together with the previous summary text into a NEW `AssistantSummary` through `completion.acomplete(...)` with the chat's own selection (low effort), a fixed English system prompt asking for a compact summary in the conversation's language that keeps decisions, agreed codes and definitions, open questions and which material was worked on, and that does NOT restate current project state (lookups provide it fresh). The summarising call is recorded like any call: its usage and cost on the summary row, the usage hook with `kind: "summary"`, and `record_ai_call` (purpose `summary`) when billing is installed. A failure to summarise is logged (class name only) and the turn proceeds without a new summary (history trimming from 3.1 still applies). Idempotent: concurrent turns cannot create two summaries for the same range (turns of one chat are serialised already; keep the check cheap and safe).
8. What the provider gets: the system prompt gains `"\n\nSummary of the earlier conversation (the older turns are not repeated):\n" + text` when a summary exists; `build_history` returns only the completed turns AFTER `up_to_sequence` (all of them when there is no summary). CLI: `prompt_data` gets a `"summary"` key and the same trimmed history; creating a new summary discards a resumed CLI session (fresh session rebuilt from summary + tail, notice `history_rebuilt` as today). The verbatim tail is never cut by the summary.
9. Visibility: the WebSocket `history` frame gains `"summary": {"up_to_seq", "text", "created_at"} | null` (latest summary) next to `turns` (all turns stay listed), the REST turns list gains the same `summary` key on its first page, and a send that created a summary first emits `{"type":"notice","code":"summarised","up_to_seq": n}`. Document in the design doc (UCM-CHAT-6 shows it as „Zusammenfassung bis hier", expandable).
10. Levels and default: each catalogue entry may carry `level` (`top|best|balanced|fast`); missing levels come from the setting `DCM_ASSISTANT_MODEL_LEVELS` (model id → level; default: `claude-fable-5-1` and `gpt-6-astra` top, `claude-opus-5-5` best, `claude-sonnet-5-5` and `gpt-6.1-sol` balanced, `claude-haiku-5-5` and `gpt-6-luna` fast; anything else `balanced`). Setting `DCM_ASSISTANT_DEFAULT_LEVEL` (default `balanced`): a new chat and the catalogue `default` use the first USABLE entry of that level, falling back to the entry flagged `default`. `validate_catalogue` keeps requiring exactly one flagged default.
11. Picker data: `GET catalogue/` returns only entries usable for the requesting user: provider configured (`availability()["providers"][driver] == "available"`) AND optional hook `DCM_ASSISTANT_ENTRY_ALLOWED(user, entry) -> bool` (default allow; an app restricts the subscription driver to the operator's account with it); creating or changing a chat with an entry that is not usable answers `invalid_selection`. Each entry carries `label` (plain name), `provider` (the driver's provider: `anthropic`, `openai`, `claude_code`), `level`, `efforts` and `estimate`: with `?chat=<id>` (owner/shared rules of `can_view_chat`) the estimate uses that chat's current context size (latest `context_tokens`, summary applied), without it a context of 0. Per effort `{"min","max","unit"}`: output tokens by effort low 300, medium 800, high 2000, max 5000 (min = half, max = double), cost via `price_usage` with the entry's model; when the app configures `DCM_ASSISTANT_COST_TO_TOKENS(user, cost_usd_micros) -> int` the unit is `"tokens"` and min/max are converted by it, otherwise `"level"` with a relative cost level 1–3 (the entry's medium-effort cost compared with the other usable entries: cheapest third 1, middle 2, most expensive 3; a single entry is 1). Document the shape.

### Tests (new files only, per chunk)

Chunk A: usage stored for each driver with mocked providers (OpenAI cached split, Anthropic cache fields, Claude Code `result` event incl. cache fields; a CLI reply without usage field records zeros, not an error), price table (each default model, the Haiku tier switch at 100000, missing cache rates, legacy fallback, settings override), cost stored on the turn across tool and repair rounds, hook called with the documented dict and never breaking a turn, behaviour without hook unchanged, `record_ai_call` uses the new prices. Chunk B: summary triggered at the limit and used instead of older turns, verbatim tail kept, summary visible in the history frame and REST, summarising cost recorded and hook kind `summary`, failure to summarise fail-soft, CLI session discarded on a new summary, levels and default level, picker filtering (provider not configured, hook denying), invalid selection of an unusable entry, estimate shapes for both units. Existing assistant tests stay green (part of the Orchestrator's run).

### Invariants
Nothing changes without the new settings/hooks except: usage now has four counts and a cost on every reply, replies via `claude_cli` record their usage, older turns above the 40k context limit are summarised (visible), the catalogue endpoint is filtered and enriched. No secrets in logs. No change to debiting, grants or tool semantics.

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

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator 2026-10-16 (in the Maana Expertenchat, session local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Seams: `assistant/turns.py` (`_finish`, usage aggregation), `assistant/drivers/*`, `assistant/history.py`, `assistant/hooks.py`, `extraction/cost_guard.py`. Tier 3: all lenses plus `sec_reviewer`. Release and consumer pin bumps per the dcm publish flow.
