# DCM-CHAT-10 — Chat core hooks for a research consumer: per-turn context, after-turn processing, availability, live access checks, session upkeep

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Stage 2 of the shared chat: it prepares `maana/MAANA-CHAT-11` (maana moves its assistant onto the core). Companion: `ui-core-micha/UCM-CHAT-3` (surface extension points).

# A. Envelope — addressed to everyone

## Goal and expected outcome

maana's research assistant (`maana/backend/assistant/`) was the template for the chat core. It cannot move onto the core yet, because the core offers only three app hooks: system prompt per chat, read-only tools and a scope permission. maana additionally needs five things:
- context computed per message;
- a record of what was sent;
- processing of the answer (change proposals) with its own live frames;
- an availability gate (research data approval);
- an access check while a turn runs.

Some of maana's session upkeep is also missing from the core.

Expected outcome: the core offers these as generic, optional hooks and core behaviours. maana can then delete its own chat plumbing and keep only its domain logic. Apps that use none of the new hooks, such as cockpit, behave exactly as before.

## Operator decisions this Envelope records (2026-10-15)

- maana moves onto the core in three orders: this one, `UCM-CHAT-3` and `MAANA-CHAT-11`.
- Where maana's own session and history mechanics go beyond the core, the capability becomes a core feature here. It does not stay a maana special path.

## Scope

**Hook 1, per-turn context:** an optional hook called once per turn, before the provider call.
- It receives the chat, the user, the message and the new turn.
- It returns a context text that the core sends with this turn's prompt, in addition to the system prompt.
- It may persist its own record for that turn, for example what data was disclosed, keyed by the turn id in the app's own tables.
- If the hook raises, the turn fails with `provider_error` and nothing goes to the provider.

**Hook 2, after-turn processing:** an optional hook called once after a turn completed.
- It receives the chat, the user, the turn and the model's text.
- It may return a replacement text that is stored and shown instead of the raw text, for example with a machine-readable block removed.
- It may also return app frames. The core delivers them over the WebSocket before the final `turn` frame, as `{"type":"app","name":<app-defined name>,"payload":<JSON>}`. The core does not interpret `name` or `payload`.
- If the hook raises, the turn still completes with the raw text, and the core logs the failure server-side.

**Hook 3, availability:** an optional hook that receives the user and a scope and returns either available or a reason code chosen by the app.
- `status/` takes an optional `scope` query parameter and reports `"status":"unavailable","reason":<code>` when the hook says so.
- Provider availability is still reported as today.
- A `send` in an unavailable scope is rejected with the error `unavailable`, before any provider call.

**Live access check during a turn:**
- While a turn runs, the core re-evaluates the scope permission at a configurable interval (default 1 second, a new limit `access_recheck_seconds`).
- It also evaluates an optional turn-validity hook, which receives the chat, the user and the running turn. With it, an app can invalidate a running turn when its own permissions narrow.
- If either fails, the turn is cancelled and the socket closes with 4403, as it does today for a failed per-frame check.

**Scoped chat lists:** `GET chats/` accepts an optional `scope` parameter and returns only that scope's chats. The scope permission applies, and a scope the user may not access returns 404. Without the parameter, behaviour is unchanged.

**Session upkeep, as core behaviour for the Claude Code driver:**
- **Token threshold:** a resumed provider session whose accumulated input tokens exceed a new limit `session_rebuild_tokens` is not resumed. The next turn starts a fresh session, rebuilt from the stored history.
- **Prompt version:** an optional app setting `DCM_ASSISTANT_PROMPT_VERSION` is stored with each provider session. When it changes, the next turn starts a fresh session.
- **History budget:** when the history is rebuilt and would exceed `max_prompt_bytes`, the oldest completed turns are left out until it fits, instead of the turn failing with `prompt_too_long`. The client receives `{"type":"notice","code":"history_trimmed","left_out":<count>}`.

**Documentation:** `docs/design/assistant-chat.md`, the `CHANGELOG` and the `webapp-management/SHARED_CAPABILITIES.md` row describe the hooks, the new frame, the new error and reason, the new parameters and the new limits.

## Non-goals

- Anything maana-specific in dcm: grants, projections, aliases, proposals, plans or their schemas.
- Tools that change data. Proposal confirmation stays an app endpoint.
- The UI (`UCM-CHAT-3`) and the maana migration (`MAANA-CHAT-11`).
- New drivers (`DCM-CHAT-2`) and per-user credentials (`DCM-CHAT-3`).
- Tools and system prompt chosen per chat scope. That is `DCM-CHAT-9`, a separate order for cockpit; maana's four lookups apply to every maana chat.

## Tier

Tier 3: a change inside shared-core that extends the WebSocket contract and the access checks.

## Risks

- **Silent behaviour change for cockpit:** every new behaviour must be off or neutral when no hook or setting is configured. The history budget and the token threshold apply only when they are reached.
- **App frames as a side channel:** the payload is app data sent to the chat owner only, over the existing owner-checked socket. No other path is added.
- **Access check cost:** a check every second runs database queries during long turns. The interval is configurable, and the check runs only while a turn is in progress.
- **Hook failures:** a raising hook must neither leak provider details nor leave a turn in `generating`. The failure rules above are part of the contract.

## Required tests to write

- Per-turn context:
  - the hook's text reaches the prompt of that turn only;
  - the hook receives the turn id;
  - a raising hook fails the turn with `provider_error`, without a provider call.
- After-turn:
  - the replacement text is stored and sent in the final `turn` frame;
  - app frames arrive before it, in the documented shape;
  - a raising hook keeps the raw text and the turn completed.
- Availability:
  - `status/?scope=` reports `unavailable` with the app's reason;
  - `send` in that scope returns `unavailable`, and no provider call happens.
- Live access check: a scope permission or turn-validity hook that starts failing during a running turn cancels it and closes with 4403 within the interval.
- Scoped list: `?scope=` returns only that scope's chats, and a forbidden scope returns 404.
- Session upkeep:
  - the token threshold forces a fresh session;
  - a changed prompt version forces a fresh session;
  - a history over budget leaves out the oldest turns and sends `history_trimmed` with the count.
- Neutrality: with none of the new hooks or settings configured, the existing assistant tests pass unchanged.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Minor release (additive contract). `DCM-CHAT-9` (scope-aware tools, cockpit) also changes `assistant/hooks.py` and the turn runner: sequence the two, or release them together, so that neither rebases over the other mid-review. `UCM-CHAT-3` can be built against this contract in parallel; `MAANA-CHAT-11` starts after both are published.
