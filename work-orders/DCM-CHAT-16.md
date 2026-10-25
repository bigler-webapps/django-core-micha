# DCM-CHAT-16 — Assistant: per-user period cost limit, runtime model enablement, cost estimate, stored tool steps

## Part A — Envelope

### Goal and expected outcome

The assistant core gains four capabilities that hram's assistant (HRAM-AI-2) needs and every other app using `django_core_micha.assistant` can use. Today (3.18.0) there is only a global daily cap (`AI_DAILY_COST_LIMIT_CENTS`), the model catalogue is fixed in settings, a chat whose entry disappears fails with `invalid_selection`, cost estimates come from a price-table formula, and tool steps are visible only while a turn is generating.

After this order:

1. **Per-user cost limit per period.** An app sets a period (day or month) and a default limit per user for chat turns. App admins can change the limit at runtime; the value is stored, not only in settings. Each user's usage in the current period, the limit and the reset date are available to the UI. At the limit a new chat turn is refused with `quota_exceeded`. One-shot completions (`complete()`) keep the existing daily guard and do not count against this limit.
2. **Runtime model enablement.** App admins switch catalogue entries on or off and choose the default among the enabled ones. Disabled entries are not offered. A chat whose entry is disabled continues on the current default instead of failing. With no entry enabled the assistant reports itself unavailable. The settings catalogue stays the source of what entries exist.
3. **Cost estimate per entry.** The catalogue response carries, per entry, the average cost per answer over the last 30 days from recorded turns, and no value while fewer than 10 answers are recorded.
4. **Stored tool steps.** The tool steps of a turn (tool name and an app-provided display label) are stored with the turn and returned with the chat history, so a reloaded chat shows them per answer.

Who counts as an app admin for these settings is decided by the app through a hook, defaulting to staff or superuser.

### Decisions taken (operator, 2026-10-25, hram assistant requirements)

- Limit and model enablement live in dcm, not in the consuming app.
- hram: 10 USD per user per month; models switchable by admins with a default; estimate shown per model.

### Scope

1. Limit: settings for period and default, a stored per-app override, usage aggregation from recorded turn costs, enforcement at turn start, a read endpoint for the current user (usage, limit, reset date) and an admin write endpoint.
2. Models: stored enablement and default, the catalogue response filtered for users and complete for admins, default fallback for chats on a disabled entry, availability when none is enabled, admin endpoints.
3. Estimate: computed from recorded turns, cached reasonably.
4. Tool steps: stored on the turn, returned in history and in the live frames as today.
5. `docs/design/assistant-chat.md`, `CHANGELOG.md`, a minor version bump, the `SHARED_CAPABILITIES.md` row.

### Non-goals

- Changing the daily guard of one-shot completions or `extraction`.
- Per-role limits or per-role model lists.
- New drivers.

### Risks

- Cost figures missing on a path (subscription CLI) must count as unknown, not zero; a turn without a cost must not let a user pass the limit unnoticed: the documented rule decides how unknown costs count.
- Disabling the default must not leave no default while other entries are enabled.

### Required tests to write

1. A user at the period limit gets `quota_exceeded` on the next turn; a new period resets; an admin change applies to the next turn; non-admins cannot change it.
2. A disabled entry is not offered to users; a chat on it continues on the default; none enabled reports unavailable; non-admins cannot change enablement.
3. The estimate is the 30-day mean over at least 10 answers and absent below.
4. Tool steps of a finished turn appear in the history after reload.
5. One-shot completions are not counted against the period limit.

## Part B — Implementation map — addressed to the implementer

**Target working directory:** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` (git root,
`main`). Use absolute paths. The checkout is shared: `src/django_core_micha/scripts/sync_secrets.py`
and `tests/test_sync_secrets.py` carry another session's uncommitted edits — do not open, format or
revert them.

### The contract is fixed — implement it exactly

`docs/design/assistant-chat.md`, section "Period cost limit, model enablement, estimates, tool steps
(DCM-CHAT-16)", plus the added `average_cost_usd_micros` in the catalogue row and `tool_steps` in the
turn fields. It fixes settings, storage, the period and unknown-cost rules, the enablement and default
rules, the estimate, every endpoint shape and error code. Do not edit `docs/`, `CHANGELOG.md`, the
version or `WORK_ORDERS.md`. If the contract is contradictory or not implementable, stop with
`RESULT: BLOCKED <the contradiction>`.

### Verified facts (Orchestrator, 2026-10-25)

- `assistant/models.py:22-45` `AssistantTurn` already has `sender`, `driver`, `model`, `state`,
  `error_code`, `usage`, `cost_usd_micros`, `tool_trace` (list of `{name, label, sources}`, appended at
  each tool `end` frame in `turns.py:349`) and `created_at`; `AssistantSummary` (`:47-57`) has
  `cost_usd_micros` and `created_at`. Latest migration: `migrations/0005_assistant_summary.py`.
- Cost: `turns.py:87-99` `_finish` stores `cost_usd_micros = price_usage(model, tokens)`; `price_usage`
  (`ai_prices.py:39`) never prices a known usage at zero, so a zero cost with zero tokens means usage
  was never reported.
- Turn start: `turns.py:65-85` `_create_turn` (inside `transaction.atomic`, chat row locked): today a
  missing selection or `validate(...)` failure raises `invalid_selection` (`:74-75`). The period check
  and the disabled-entry fallback belong here, before the turn row is created; switching the chat's
  selection is persisted on the locked chat row.
- Catalogue: `catalogue.py:11-58` (`entries`, `entry_usable`, `usable_entries`, `default_entry`,
  `entry_by_id`, `validate`). Enablement filters `usable_entries`/`default_entry` for users; the
  admin view needs every settings entry. `catalogue_check` (`:78-88`) is where `dcm_assistant.E002`
  for a malformed `DCM_ASSISTANT_COST_LIMIT` goes.
- Status: `availability.py:5-12` and `views.py:80-89` `AvailabilityView`.
- Views: `views.py:40-74` `CatalogueView` (add `average_cost_usd_micros` per entry; keep `estimate`
  unchanged), `views.py:450ff` turn JSON comes from `turns.py:450-464` `turn_payload` (add
  `tool_steps`). URLs: `urls.py:3`. Error shape: `views.py:37` `_problem(code, status)`.
- Hooks: `hooks.py:199-201` `_hook("NAME")` resolves `DCM_ASSISTANT_<NAME>` (dotted path or callable);
  use it for `SETTINGS_ADMIN`.
- Audit: `audit_config.py` registers assistant models; register `AssistantConfig` there.
- Cache: use Django's default cache for the estimate (key per entry id, 10 minutes).

### Files

- `assistant/models.py` (`AssistantConfig`), new `assistant/migrations/0006_assistant_config.py`
  (hand-written in the style of `0005`; the Orchestrator verifies with `makemigrations --check`),
  new `assistant/limit.py` (period window, usage, unknown-cost rule, config load), `catalogue.py`,
  `availability.py` or `views.py` (the `no_model_enabled` status), `turns.py`, `views.py`, `urls.py`,
  `audit_config.py`.
- Tests: new `src/django_core_micha/assistant/tests/test_limit_models.py` (fixture patterns:
  `assistant/tests/test_completion.py:1-15` for the catalogue override and users; the REST tests in
  `assistant/tests/test_core.py` for authenticated API calls).

### Required tests (Part A) — how to pin them

1. Limit: seed turns with costs for a user inside and outside the current period (use
   `freezegun`-free approaches: create rows with explicit `created_at` via `update()`); at the limit
   the next turn start raises `quota_exceeded` and creates no row; a turn from the previous period does
   not count; an admin PATCH raising the limit lets the next start through; a non-admin PATCH is 403;
   `GET /assistant/limit/` returns usage, limit and `resets_at` for both periods (day and month
   boundaries in a non-UTC `TIME_ZONE`); unknown-cost turns count at the entry average or the fallback,
   and pre-provider failures (`not_configured`) count zero.
2. Models: a disabled entry is absent from the user catalogue and present (with `enabled: false`) in the
   admin view; a chat on a disabled entry starts its next turn on the default and the chat row now
   carries the default; all disabled → status `unavailable`/`no_model_enabled`, empty catalogue,
   turn start `not_configured`; disabling the default without a new one → 400 `default_disabled`;
   non-admins get 403 on both admin endpoints.
3. Estimate: 9 completed turns → `null`; 10 → their integer mean; turns older than 30 days and turns
   without usage are excluded.
4. Tool steps: a finished turn with a tool trace returns `tool_steps` in `GET .../turns/`.
5. One-shot: a `complete()` call (fake driver as in `test_completion.py`) leaves the period usage
   unchanged.

Run only the tests you wrote, from the target working directory:
`python -m pytest src/django_core_micha/assistant/tests/test_limit_models.py -q -p no:cacheprovider`

### Invariants and pitfalls

- No change to `extraction/cost_guard.py`, one-shot completion, drivers, the WebSocket frame shapes or
  any existing response key.
- Admin endpoints check the admin hook on every method; anonymous users get DRF's 401/403 as today.
- Files are UTF-8: open them with explicit `encoding="utf-8"`.

### Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

Work from this package; do not explore broadly from scratch; open only the named files to verify.
Schema (one migration) and admin endpoints ARE in this WO's scope, exactly as mapped.

## Part C — Orchestrator-only

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

- Execution: `codex/gpt-6-luna` CLI, reasoning high, WO on stdin, `-C` the target directory. Launch
  evidence: one dispatch 2026-10-25, banner read back (`model: gpt-6-luna`, reasoning high, workdir the
  target directory), exit 0 with `RESULT: DONE`; no commit or register edit by the implementer.

- Tier 3 (a change inside shared-core, a new migration, new admin endpoints): all reviewers incl. `sec_reviewer`, full context.
- Release per dcm's publish-from-main topology; consumers bump in their own WOs (hram: HRAM-AI-2).
- Pairs with UCM-CHAT-9 (the UI side); the response shapes defined here are what UCM-CHAT-9 consumes.
- The working tree may carry foreign uncommitted changes (`scripts/sync_secrets.py`, `tests/test_sync_secrets.py`); commit only own files.
