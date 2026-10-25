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

**Not dispatch-ready.** The Orchestrator fills the verified context package before dispatch.

**Target working directory:** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

### Named orientation files

- `src/django_core_micha/assistant/catalogue.py`, `availability.py`, `limits.py`, `turns.py`, `models.py`, `views.py`, `urls.py`, `hooks.py`, `history.py`, `consumers.py`
- `src/django_core_micha/extraction/cost_guard.py` (existing daily guard, `reserve_user_budget`)
- `docs/design/assistant-chat.md`

### Preamble

> Part A is the complete scope. Do not edit WORK_ORDERS.md; do not install dependencies, change lockfiles or tidy foreign changes. Do not git add/commit/push, self-review or treat your own run as the gate. Stop and report an out-of-scope cause. The Orchestrator supplies the progress contract and runtime-specific preamble.

## Part C — Orchestrator-only

**STOP — this section is for the Orchestrator, not a dispatched implementer.**

- Tier 3 (a change inside shared-core, a new migration, new admin endpoints): all reviewers incl. `sec_reviewer`, full context.
- Release per dcm's publish-from-main topology; consumers bump in their own WOs (hram: HRAM-AI-2).
- Pairs with UCM-CHAT-9 (the UI side); the response shapes defined here are what UCM-CHAT-9 consumes.
- The working tree may carry foreign uncommitted changes (`scripts/sync_secrets.py`, `tests/test_sync_secrets.py`); commit only own files.
