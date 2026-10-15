# DCM-CHAT-1 — Shared AI chat core: persistent chats, three model drivers, read-only tools

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Companion orders: `ui-core-micha/UCM-CHAT-1` (the chat surface), `cockpit/CP-CHAT-4` (first consumer). Follow-ups registered here: `DCM-CHAT-2` (hpc-bridge driver), `DCM-CHAT-3` (per-user credentials); `maana/MAANA-CHAT-11` (maana migrates onto this core).

# A. Envelope — addressed to everyone

## Goal and expected outcome

Two apps already run their own chat with a language model: maana (`backend/assistant/`, a research assistant over project data) and survey_app (`backend/surveys/services/assistant.py`, the AI questionnaire builder). cockpit needs the same capability. This order lifts the domain-independent part into dcm, with maana's implementation as the template, so that an app gets a persistent, streaming chat by installing one module and supplying its prompt, its tools and its scope rule.

Expected outcome: dcm ships an opt-in module that provides persistent chats per user, a turn lifecycle that survives worker restarts, a WebSocket stream, a REST surface, three interchangeable model drivers (Claude Code CLI on a subscription token, OpenAI API, Anthropic API), a per-chat choice of model and effort from an app-defined catalogue, and read-only tools the app registers. The app supplies only domain knowledge: system prompt, tools, scope rule, catalogue.

## Operator decisions this Envelope records (2026-10-15)

- **Scope of the lift:** chat core plus UI. Proposals, grants, structured JSON turns and every other domain-specific piece stay in the apps.
- **Drivers in v1:** Claude Code CLI (subscription), OpenAI API, Anthropic API. The hpc-bridge (local models) follows as `DCM-CHAT-2`.
- **Chat form:** several persistent chats per user, with a chat list, an automatic title, rename, archive and delete. Only the owner can access a chat.
- **Model choice:** per chat, changeable at any time, effective from the next turn. The picker spans drivers: one catalogue of driver + model entries, defined by the app. Effort is chosen alongside the model.
- **Sources:** each turn records which tools ran and which sources they returned. The UI shows those as chips under the answer.
- **Per-user credentials (own login or own API key)** are stage 4 (`DCM-CHAT-3`). This order builds only the two seams for it (see Scope).
- **Consumers:** cockpit first (`CP-CHAT-4`), then maana (`MAANA-CHAT-11`). Whether survey_app migrates is decided after maana.

## Scope

**Module and packaging**
- A new module `django_core_micha.assistant` with the explicit app label `dcm_assistant`. That label must collide neither with maana's app `assistant` nor with cockpit's app `chat`.
- Opt-in: an app adds the module to `INSTALLED_APPS` and mounts its URL and WebSocket routes. It is **not** added to `CORE_APPS`. Apps that do not opt in get no tables and no routes.
- No new hard Python dependency. `openai` and `anthropic` are already dependencies of dcm, and the tool bridge for the CLI uses the standard library only.
- The Claude Code CLI itself (Node plus `@anthropic-ai/claude-code`) cannot ship via pip. The consuming app installs it in its image. If the CLI driver is selected and the binary is missing, the module reports "not configured" instead of failing at turn time.

**Persistence**
- **Chat:** owner, optional scope (an app-defined string key, empty when the app has no scopes), title with its source (automatic or set by the user), archived timestamp, current selection (catalogue entry plus effort), the driver session reference, and created/updated timestamps.
- **Turn:** chat, sequence, user text, assistant text, and state (`generating`, `completed`, `failed`, `cancelled`, `interrupted`). Each turn also records the driver, model and effort it ran with, the billing source, token usage, the tool trace with its sources, an error code, timestamps and a heartbeat.
- At most one generating turn per chat, enforced in the database.
- Content fields are registered with the dcm audit log and redacted, following maana's `audit_config.py`.
- Deleting a chat removes its turns and its CLI session directory.
- An archived chat is read-only until it is restored.

**Turn lifecycle (template: maana `turns.py`)**
- Each turn moves through the states above.
- A running turn sends a heartbeat.
- Turns that a dead worker left in `generating` are reconciled to `interrupted`, both on access and through a management command.
- A running turn can be stopped.
- The title is set automatically from the first message unless the user has renamed the chat.
- When the selected driver differs from the one the chat's provider session belongs to, the next turn rebuilds the history from the database instead of resuming. The same happens when the session is missing or invalid.

**Drivers**
- **Common interface:** all three drivers stream text deltas, report tool activity, honour cancellation, and return usage. They map provider failures onto one error taxonomy: quota or usage limit reached, provider error, timeout, prompt too long, invalid output, security violation, not configured.
- **Claude Code CLI**, template maana `runner.py`. The defaults:
  - The prompt goes on stdin; stdin is otherwise detached.
  - `--tools ""` removes the built-in tools. The registered tools are offered only through a local MCP bridge with an exact allow list.
  - The CLI's init event must show exactly the offered tools and servers, or the turn aborts as a security violation.
  - The environment follows an allow list and never contains `ANTHROPIC_API_KEY`.
  - Each chat gets an isolated config and session directory with mode 0700 under a configurable root. Sessions resume when valid.
  - stderr is logged server-side only and never reaches the client.
  - The process tree is killed when the turn is stopped or fails.
- **OpenAI API** and **Anthropic API:** streaming, with native function calling for the registered tools. Every provider call within a turn goes through the existing `extraction/cost_guard` reservation and settlement, so the per-user daily spend cap applies to chat as it does to extraction.

**Model catalogue and effort**
- The app declares a catalogue in settings. Each entry has a driver, a model id, a display name, the allowed effort levels and a billing tag (subscription or pay per use). One entry is the default.
- Effort is one normalised scale (`low`, `medium`, `high`, `max`). Each driver maps it onto its own parameter: the CLI's `--effort`, Anthropic's thinking/effort setting, OpenAI's reasoning effort.
- The server validates every selection against the catalogue. An entry or effort level outside the catalogue is rejected.
- The module refuses to start if a pay-per-use catalogue entry has no row in the cost guard's rate table.

**App hooks (the only places domain knowledge enters)**
- System prompt builder: per chat, may use the scope.
- Tool registry. Each tool has a name, a human-readable activity label, a description, a JSON input schema and a handler. The handler receives the requesting user, the chat and the arguments. It also receives the **trust class** of the active driver (`cloud` for all three v1 drivers, `local` reserved for `DCM-CHAT-2`), so that the app can restrict what leaves the server. Handlers return content for the model plus a list of sources (label and reference). v1 tools are read-only by contract.
- Scope permission check: decides whether a user may open chats in a given scope. Ownership of a chat is always enforced by the core in addition.
- Catalogue and limits, as settings.

**Credential seams for stage 4**
- Every turn obtains its credential through one resolver function. In v1 the resolver always returns the deployment's credential for the selected driver.
- Every turn stores its billing source (`deployment` in v1). No credential storage and no UI in this order.

**Transport contract (binding for `UCM-CHAT-1` and every consumer)**
- **WebSocket:** a consumer on `BaseSecureConsumer` (S112), owner-only per chat.
  - Client frames: `send` (message text) and `stop`. Model and effort changes go over REST, never over the socket.
  - Server frames: `history` on connect, `delta` (text), `tool` (activity start and end, with the tool's label and its sources), `turn` (the final turn: text, state, sources, driver, model, effort, usage), `notice`, `error` (a code from the taxonomy above plus `rate_limited`, `invalid_selection`, `archived`).
  - Close codes: 4401 (session ended), 4403 (access lost), 4429 (rate limited).
- **REST:** these endpoints, all owner-only:
  - the catalogue with its default;
  - the chat list with a cursor and an archived filter;
  - create chat;
  - update chat: title, archived state, selection;
  - delete chat;
  - turns of a chat, with a cursor;
  - provider availability (`available`, `not_configured`).

**Limits:** maana's `limits.py` values become the defaults, overridable by settings:
- frame rate per window;
- concurrent turns deployment-wide and per user;
- maximum message length, prompt bytes and output bytes;
- provider timeout and idle timeout;
- heartbeat and stale-turn intervals;
- maximum tool calls per turn.

**Documentation:** a design document under `docs/design/` describing the module, the hooks and the transport contract. A row for the capability in `webapp-management/SHARED_CAPABILITIES.md`, as a small companion change in that repository. The `CHAT-*` prefix in this repository's register.

## Non-goals

- Tools that change data, and any confirm-a-proposal framework (maana's proposals stay in maana).
- The hpc-bridge driver (`DCM-CHAT-2`) and per-user credentials (`DCM-CHAT-3`).
- Migrating maana or survey_app. Neither repository is touched by this order.
- survey_app's structured JSON turn with a repair loop, as a core mode.
- The UI (`UCM-CHAT-1`).
- Search across chats, sharing chats, attachments or file upload.
- Any use of the `messaging` module. Chat transcripts are not human-to-human messages.
- Changes to `extraction/` beyond calling its cost guard. Its drivers stay as they are.

## Tier

Tier 3: new code inside shared-core, a new WebSocket surface, a subprocess with a credential in its environment, and spend control.

## Risks

- **Data leaving the server:** tool results go to a cloud provider. The trust class passed to every handler is the control. An app that ignores it can leak data it would not otherwise send (cockpit's knowledge base has a hard `internal` ceiling for cloud models).
- **Prompt injection through tool results:** retrieved documents may contain instructions. Because every v1 tool is read-only and the CLI runs without built-in tools, the damage is limited to a misleading answer.
- **CLI behaviour drift:** the init-event shape and flags change between Claude Code versions, and apps pin their own versions (survey_app 2.1.210, maana 2.1.280). The security assertion must fail closed when the event shape is unknown.
- **Spend:** a long tool loop on a pay-per-use driver costs real money. The cost guard and the maximum tool calls per turn bound it. The cost guard relies on an atomic cache backend, an existing precondition of `extraction/`.
- **Release chain:** two libraries must publish before the first consumer can adopt. A transport change after publication forces a second release across all of them, which is why the contract is fixed here.
- **App label collision:** a label equal to an existing app label breaks migrations in that app. The explicit label above avoids it.

## Required tests to write

- Turn lifecycle:
  - one generating turn per chat;
  - stale reconciliation;
  - stop;
  - the automatic title not overwriting a user title;
  - history rebuild after a driver switch.
- CLI driver, with a fake process and no real CLI:
  - environment allow list without `ANTHROPIC_API_KEY`;
  - init-event mismatch aborts;
  - quota detection;
  - timeout kills the process tree;
  - the prompt is passed on stdin.
- API drivers, with mocked SDK clients:
  - streamed deltas;
  - a tool call round trip;
  - cost-guard reservation and settlement per provider call;
  - a blocked call when the cap is reached.
- Catalogue:
  - a selection outside the catalogue is rejected;
  - an effort level the entry does not allow is rejected;
  - a pay-per-use entry without a rate-table row fails at startup.
- Tools: the handler receives the trust class, and sources from the tool trace appear in the final `turn` frame.
- Consumer and REST:
  - `assert_all_consumers_secure` includes the new consumer;
  - another user's chat is unreachable over both REST and the socket;
  - a write to an archived chat is refused;
  - delete removes the session directory;
  - the frame-rate limit closes with 4429.
- Credential seam: the resolver is called once per turn, and the billing source is stored.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Publish as a new minor version at the end of the order; `UCM-CHAT-1` can be built against the transport contract in parallel, and `CP-CHAT-4` starts after both publications.
