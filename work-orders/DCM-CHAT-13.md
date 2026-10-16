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
6. **Picker data:** the model catalogue offered to a person contains only entries usable for that account and app (provider configured, account allowed, subscription driver only for the operator's own account); each entry carries its plain name, its provider, its level (top / best / balanced / fast; operator setting per model: Fable and Astra top, Opus best, Sonnet and Sol balanced, Haiku and Luna fast; all configured models are offered), its supported efforts, and an estimate range per effort for the current chat length in the app's token unit (or a relative cost level 1–3 when the app has no tokens). The estimate uses the cost table and the chat's current context size.

### Scope and acceptance

Usage recording for all drivers, summarisation with the limit setting, the hook, the payload fields; existing chats keep working.

### Non-goals and do-not-touch

Token prices and debiting (apps), model choice, grants and tool semantics, per-user API keys.

### Risks

A summary that drops a decision changes the assistant's behaviour silently; the summary must be visible and the verbatim tail long enough. Summarising itself costs tokens and must be recorded too.

### Required tests to write or update

Usage stored for each driver (provider mocked), cost computed from the table, summary triggered at the limit and sent instead of older turns, verbatim tail kept, summary visible in history, hook called with usage, behaviour unchanged without hook.

## Part B — Implementation map (implementer; Orchestrator completes before dispatch)

Placeholder — dispatch-blocking. The Orchestrator fills the context package, target directory, progress contract and implementer preamble before any dispatch.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 pending. Seams: `assistant/turns.py` (`_finish`, usage aggregation), `assistant/drivers/*`, `assistant/history.py`, `assistant/hooks.py`, `extraction/cost_guard.py`. Tier 3: all lenses plus `sec_reviewer`. Release and consumer pin bumps per the dcm publish flow.
