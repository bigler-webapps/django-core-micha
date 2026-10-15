# DCM-CHAT-11 — Chat core for a structured consumer: client context, structured turns with repair rounds, one-shot completions

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Stage 2 of the shared chat for survey_app: it prepares `survey_app/SVA-AIB-45`. Companions: `DCM-CHAT-12` (shared chats in a scope), `ui-core-micha/UCM-CHAT-4`. Builds on `DCM-CHAT-10` (3.1.0).

# A. Envelope — addressed to everyone

## Goal and expected outcome

survey_app's AI questionnaire builder (`survey_app/backend/surveys/services/assistant.py`, `consumers.py`) has three needs the chat core does not cover:
- **Client context:** every message carries the editor's current, possibly unsaved draft from the browser.
- **Structured turns:** the model answers with one JSON object, which the app validates. An invalid draft is sent back for repair, up to two rounds, before the answer is accepted.
- **Single calls:** two calls are not chats at all: a resolution suggestion for a merge conflict, and a translation of the consent text.

Expected outcome: the core accepts an app-validated context with a message, runs structured turns with repair rounds instead of streaming raw text, and offers a single completion call without a chat. All of it goes through the same drivers, credentials, limits and cost guard. Apps that use none of it behave exactly as before.

## Operator decisions this Envelope records (2026-10-15)

- **Client context:** the context travels with the message (`send` frame) and is validated by the app. It is not saved beforehand.
- **Display:** during a structured turn the user sees the typing state and then the finished answer. No raw JSON is streamed.
- **Single calls:** they move into the core. survey_app's own CLI runner goes away entirely.

## Scope

- **Client context:**
  - The `send` frame accepts an optional `context` (a JSON object).
  - The core bounds its size with a new limit `max_client_context_bytes`. An oversized context is rejected with `invalid_frame`.
  - An optional app hook validates the context and may normalise it. If it rejects the context, the send ends with `invalid_frame` before any turn row or provider call.
  - The validated context reaches the per-turn context hook from `DCM-CHAT-10`. Hooks written for 3.1 keep working unchanged.
  - The context is not stored as part of the user text or the history.
- **Structured turns:**
  - With a new app setting, an app declares that its turns are structured. The core then sends no `delta` frames for that app's turns; clients show the typing state until the final `turn` frame.
  - The after-turn hook from `DCM-CHAT-10` may additionally return a repair request with feedback text. The core then sends the feedback to the model as a follow-up within the same turn: a resume for the Claude Code driver, the next message for the API drivers. It calls the after-turn hook again on the new answer.
  - A new limit `max_repair_rounds` (default 2) bounds the rounds. After the last round, the hook must accept, or it supplies its own fallback text and frames.
  - Every round counts against the cost guard and the existing time and size limits. Only the accepted result is stored as the turn's text.
- **Single completion:**
  - A server-side function sends one prompt to one catalogue entry, with optional system text and effort, and returns the text and usage. There is no chat, no persistence and no tools.
  - It uses the credential resolver for the calling user, the cost guard, the provider timeout and the output bound, and maps errors onto the existing taxonomy.
  - For the Claude Code driver it runs in a throwaway session directory that is removed afterwards. It is a Python API for app code, not a REST endpoint.
- **Documentation:** `docs/design/assistant-chat.md`, the `CHANGELOG` and the `webapp-management/SHARED_CAPABILITIES.md` row.

## Non-goals

- Shared chats in a scope (`DCM-CHAT-12`).
- Any survey-specific schema, prompt or validation in dcm.
- Streaming a field out of partial JSON.
- A REST endpoint for single completions.

## Tier

Tier 3: a change inside shared-core that extends the WebSocket contract and adds a provider call path.

## Risks

- **Client context as an input channel:** it is user-supplied data that reaches the model. The size bound and the app's validation hook are the controls. The core never interprets it.
- **Repair loops and spend:** every round costs time and money. `max_repair_rounds`, the cost guard and the provider timeout bound it.
- **Neutrality:** cockpit and maana must see no change. Structured mode, client context and repair are off unless configured or sent.

## Required tests to write

- Client context:
  - an accepted context reaches the per-turn context hook;
  - an oversized one is rejected with `invalid_frame`;
  - a context the app hook rejects ends the send with `invalid_frame`, without a turn row or provider call;
  - 3.1-style context hooks keep working.
- Structured turns:
  - no `delta` frames are sent;
  - a repair request triggers exactly one follow-up per round, up to `max_repair_rounds`;
  - the accepted text is stored;
  - the last round's fallback is used when the hook keeps asking for repair;
  - each round passes through the cost guard.
- Single completion, for each driver with fakes:
  - text and usage are returned;
  - the cost guard is applied;
  - a timeout maps to `timeout`;
  - the Claude Code session directory is removed afterwards.
- Neutrality: without the new settings, the existing assistant tests pass unchanged.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Same minor release as `DCM-CHAT-12`. `DCM-CHAT-9` (scope-aware tools) also touches the hooks and the turn runner: sequence the three so none rebases mid-review.
