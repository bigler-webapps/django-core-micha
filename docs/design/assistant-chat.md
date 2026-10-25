# Shared assistant chat contract

`django_core_micha.assistant` is opt-in. Add it to `INSTALLED_APPS`, mount `django_core_micha.assistant.urls` under an application-selected REST prefix, and include `django_core_micha.assistant.routing.websocket_urlpatterns` in the authenticated Channels router. The app owns prompts, read-only tools, scope authorization, catalogue entries and limit overrides. The core enforces scope access; chats are owner-only unless the app explicitly shares a non-empty scope.

## App hooks and settings

- `DCM_ASSISTANT_SYSTEM_PROMPT_BUILDER(chat) -> str`
- `DCM_ASSISTANT_TOOL_REGISTRY -> iterable[ToolSpec]`; each tool has `name`, `label`, `description`, `input_schema`, and `handler(user, chat, arguments, trust_class) -> (content, sources)`. A handler may be synchronous or asynchronous. Synchronous handlers run in a worker thread and may use the Django ORM; asynchronous handlers are awaited. `sources` is a list of `{label, reference}` records. v1 trust class is always `cloud`.
- `DCM_ASSISTANT_TOOLS_FOR_CHAT(chat) -> iterable[ToolSpec] | None`: optionally choose the exact read-only tool set offered to each chat. The chat includes its `scope`. A returned iterable, including an empty one, is that chat's complete tool set. Returning `None` uses `DCM_ASSISTANT_TOOL_REGISTRY` for an unscoped chat and offers no tools for a scoped chat. If this hook is unset, the global registry applies to every chat, preserving existing behavior. Tool names and input schemas use the same validation as the global registry. Tool handlers receive the chat and can use `chat.scope` to bind reads to that scope. Build a scope-specific system prompt with the existing `DCM_ASSISTANT_SYSTEM_PROMPT_BUILDER(chat)` hook.
- `DCM_ASSISTANT_SCOPE_PERMISSION(user, scope) -> bool`; empty scope is allowed by default.
- `DCM_ASSISTANT_SHARED_SCOPE(scope) -> bool`; optional. Exceptions, missing hook, and empty scopes are never shared.
- `DCM_ASSISTANT_CLIENT_CONTEXT(chat, user, context) -> dict`; optional validation and normalization for client-supplied context. Rejection is returned as `invalid_frame`.
- `DCM_ASSISTANT_TURN_CONTEXT(chat, user, message, turn[, client_context]) -> str | None`; called before each provider turn. Its text is sent only with that turn and is not stored in history.
- `DCM_ASSISTANT_AFTER_TURN(chat, user, turn, text) -> None | str | AfterTurnResult`; may replace the saved answer and return app frames. `AfterTurnResult` has `text`, `frames`, and optional `repair` feedback. A repair asks the core to make a follow-up provider call; after `max_repair_rounds` the hook result is used as fallback (its text, or the last raw answer). each frame has `name` and JSON-serialisable `payload`.
- `DCM_ASSISTANT_AVAILABILITY(user, scope) -> None | str`; a non-empty reason code makes the scope unavailable.
- `DCM_ASSISTANT_TURN_VALIDITY(chat, user, turn) -> bool`; checked during a running provider turn.
- `DCM_ASSISTANT_USAGE_HOOK(chat, user, turn, usage)`; optional, called after a turn finishes when usage was reported. `usage` contains `kind` (`reply`), `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd_micros`, and `context_tokens`. Hook failures are logged by exception class and do not affect the turn.
- `DCM_ASSISTANT_ENTRY_ALLOWED(user, entry) -> bool`; optional catalogue filter, default allow. Provider availability is also required. `DCM_ASSISTANT_MODEL_LEVELS` maps model ids to `top|best|balanced|fast`; `DCM_ASSISTANT_DEFAULT_LEVEL` defaults to `balanced`.
- `DCM_ASSISTANT_COST_TO_TOKENS(user, cost_usd_micros) -> int`; optional conversion for picker estimates.
- `DCM_ASSISTANT_PROMPT_VERSION`: optional integer or string. Changing it rebuilds the next Claude CLI session.
- `DCM_ASSISTANT_CATALOGUE`: list of `{driver, model, name, efforts, billing, default}` where driver is `claude_cli`, `openai_api`, or `anthropic_api`; effort is from `low|medium|high|max`; billing is `subscription|pay_per_use`; exactly one entry has `default: true`.
- `DCM_ASSISTANT_LIMITS`: optional mapping overriding `frame_rate_window`, `frame_rate_count`, `max_concurrent_turns`, `max_turns_per_user`, `max_message_length`, `max_prompt_bytes`, `max_output_bytes`, `provider_timeout`, `idle_timeout`, `heartbeat_seconds`, `stale_turn_seconds`, `max_tool_calls`, `access_recheck_seconds` (default 1), `session_rebuild_tokens` (default 200000), `max_client_context_bytes` (default 65536), `max_repair_rounds` (default 2), `context_limit_tokens` (default 40000), and `summary_keep_turns` (default 6).
- Deployment credential settings: `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`. Each turn invokes `resolve_credential(user, driver)` once. Per-user credentials are deferred. API calls reserve and settle through extraction's cost guard; reservation denial is `quota_exceeded`.

`DCM_ASSISTANT_STRUCTURED_TURNS=True` suppresses `delta` frames and sends only the final answer.

`django_core_micha.assistant.completion.acomplete(user, *, prompt, system="", entry=None, driver=None, model=None, effort=None, purpose="completion", response_schema=None)` and its synchronous `complete` wrapper make one bounded, non-persistent completion through the catalogue, credential resolver, timeout, and cost guard. The result is `CompletionResult(text, usage, data)`; without `response_schema`, `data` is `None` and everything else is unchanged.

**Structured completion (DCM-CHAT-15).** With `response_schema`, `data` is a JSON object that has been parsed and validated against the schema. A schema must pass `extraction.schema_contract.validate_schema` and the completion subset, otherwise `DriverError("invalid_schema")` is raised before any provider call:
- the root is `type: "object"`;
- only the keywords `type`, `properties`, `required`, `additionalProperties`, `items`, `enum`, `const`, `anyOf`, `description` and `title` are used;
- every object has `additionalProperties: false`, and its `required` lists all of its properties (an optional value is an `anyOf` with `{"type": "null"}`).

How each driver produces the answer:
- `openai_api` sends the schema as Responses `text.format` (`json_schema`, strict).
- `anthropic_api` sends it as `output_config.format`.
- `claude_cli` passes `--json-schema` when the installed CLI offers it, and otherwise puts the schema in the prompt.

The core validates every answer itself. If an answer is not valid JSON or fails validation, the core makes up to `max_repair_rounds` repair calls (default 2), each carrying the validation error; after that it raises `DriverError("invalid_output")`. Every call keeps its own provider timeout and output cap and goes through the driver's cost guard, so a denied reservation is `quota_exceeded`. `usage` is summed over all calls and recorded once.

## REST

All paths below are relative to the mount prefix. Every endpoint requires authentication. Chat reads, writes, turns and deletes are owner-only and scope-checked by default; in a scope the app declares shared (`DCM_ASSISTANT_SHARED_SCOPE`) every user who passes the scope permission may read, send, rename, re-select, archive and restore, and only the creator may delete (others get 403 `forbidden`). Missing chats, and foreign chats in an owner-only scope, return 404.

| Method and path | Contract |
| --- | --- |
| `GET /assistant/catalogue/?chat=<id>` | `{ "default": {"entry": entry_id, "effort": effort}|null, "entries": [entry] }`. Entries are limited to configured providers and entries allowed for the user. Entries are also limited to enabled entries (DCM-CHAT-16). Entry: `{id, driver, model, label, provider, level, efforts, billing, default, estimate, average_cost_usd_micros}` where `id` is `"<driver>:<model>"`, provider is `anthropic|openai|claude_code`, level is `top|best|balanced|fast`, and estimate maps each supported effort to `{min,max,unit}`. Unit is `tokens` with the cost conversion hook, otherwise `level` (relative 1–3). A chat query uses its latest context size; otherwise context is zero. |
| `GET /assistant/status/?scope=<scope>` | When the availability hook returns a reason: `{ "status":"unavailable", "reason":<app code>, "providers":{...} }`; otherwise the provider status contract below applies. A scope the user may not access answers 404. |
| `GET /assistant/status/` | `{ "status": "available"|"not_configured", "providers": { "claude_cli": "available"|"not_configured", "openai_api": ..., "anthropic_api": ... } }`; `status` is `available` when any provider is usable. |
| `GET /assistant/chats/?cursor=&limit=&archived=false&scope=` | `{ "results": [chat], "next": string|null, "has_more": bool }`; optional scope filters the list and must pass scope permission (otherwise 404). The cursor is bound to scope. |
| `POST /assistant/chats/` | JSON `{title?, scope?, driver?, model?, effort?}`; returns chat, 201. Omitted selection uses catalogue default and its first allowed effort. |
| `GET/PATCH/DELETE /assistant/chats/{chat_id}/` | PATCH accepts title, archived boolean, and/or a selection, either `{"selection": {"entry": entry_id, "effort": effort}}` (the surface form) or `driver`/`model`/`effort`; an entry outside the catalogue or an effort it does not allow returns 400 `invalid_selection`. Restore an archived chat with `{"archived":false}`. Other archived writes return 409 `archived`. DELETE returns 204 and removes the CLI session directory. |
| `GET /assistant/chats/{chat_id}/turns/?cursor=&limit=` | `{ "results": [turn], "next": string|null, "has_more": bool }`, oldest-to-newest within each page; the first page also has `summary` (`{up_to_seq,text,created_at}` or null). |

Shared-scope access follows this rule table (scope permission is required in every row):

| Action | Shared scope | Owner-only scope |
| --- | --- | --- |
| List, read, send, rename, re-select, archive, restore | Any user allowed in the scope | Creator |
| Delete | Creator only; other viewers receive 403 `forbidden` | Creator |

Turn payloads in shared scopes include `sender_id`. The sender owns credential and cost attribution. Usage contains normalized non-cached `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd_micros` (integer micro-USD), and `context_tokens` (prompt tokens from the last provider call). Shared WebSocket frames are group-delivered and each socket rechecks its own scope access before delivery. A `stop` frame cancels only the turn started on that same connection.

Chat JSON fields: `id`, `scope`, `title`, `title_source`, `archived_at`, `driver`, `model`, `effort`, `selection` (`{entry, effort}` or null when the stored driver/model is no longer in the catalogue), `created_at`, `updated_at`. Turn JSON fields: `id`, `seq`, `user_text`, `assistant_text`, `state`, `error_code`, `sources`, `tool_steps` (DCM-CHAT-16), `driver`, `model`, `effort`, `usage`, `billing_source`. Timestamps are ISO-8601 UTC strings. Usage always carries `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd_micros`, and `context_tokens`, with zero values when the provider omits details.

Cursors are opaque Django-signed values with a one-hour lifetime. Their signed body binds the cursor to the endpoint kind, owner, chat (for turn pages), archive filter and page size. Clients must pass the returned cursor unchanged; malformed, expired, tampered or cross-query cursors return HTTP 400 `{ "code":"invalid_cursor", "error":"invalid_cursor" }`.

REST errors use `{ "code": code, "error": code }`. Selection errors return 400 `invalid_selection`; archived writes return 409 `archived`; foreign or missing resources return 404.

## Period cost limit, model enablement, estimates, tool steps (DCM-CHAT-16)

All four are additive. An app that configures nothing and never calls the admin endpoints sees today's behaviour, except that turn payloads gain `tool_steps` and catalogue entries gain `average_cost_usd_micros`.

**Settings.**
- `DCM_ASSISTANT_COST_LIMIT = {"period": "day"|"month", "usd_micros": <int ≥ 0>}` switches the per-user period limit on. Absent means no limit. A malformed value is the system check error `dcm_assistant.E002` and fails closed at runtime: it acts as a daily limit of 0, so every turn is refused until the setting is fixed.
- `DCM_ASSISTANT_UNKNOWN_TURN_COST_USD_MICROS` (default `50000`) is the fallback cost of a turn whose usage was never reported (rule below).
- `DCM_ASSISTANT_SETTINGS_ADMIN` is a dotted path or callable `(user) -> bool` deciding who is an app admin for these settings. The default is `user.is_staff or user.is_superuser`.

**Stored configuration.** One row (`AssistantConfig`, migration `dcm_assistant.0006_assistant_config`) holds three values:
- `limit_usd_micros`: null means the settings default applies.
- `disabled_entries`: catalogue entry ids. Empty means every settings entry is enabled, so an entry newly added to the settings is enabled.
- `default_entry`: blank means the settings default rule applies.

The settings catalogue stays the only source of which entries exist. Stored ids that are no longer in it are ignored.

**Period and usage.**
- **Periods** follow the server's `TIME_ZONE`. A day runs from local midnight to the next one; a month runs from the first of the month, local midnight.
- **What counts:** a user's usage is the sum over the current period of the `cost_usd_micros` of turns they sent (`sender`; a turn without a sender belongs to the chat owner), plus the `cost_usd_micros` of summaries of chats they own.
- **Unknown cost:** a turn counts at an "unknown" cost when it reached the provider but ended without reported usage: state `failed`, `cancelled` or `interrupted`, all token counts zero, and `error_code` not one of `not_configured`, `invalid_selection`, `access_lost`, `archived`, `rate_limited`, `turn_active` or `quota_exceeded`. It counts at its entry's `average_cost_usd_micros` when known, otherwise at `DCM_ASSISTANT_UNKNOWN_TURN_COST_USD_MICROS`. A missing cost is therefore never zero.
- **Enforcement:** a new chat turn whose user's usage is at or above the limit is refused with `quota_exceeded` before the turn row is created. The turn already running may finish above the limit.
- **Not counted:** one-shot completions (`complete()`) create no turns, keep the existing daily guard and do not count.

**Model enablement.**
- **Enabled set:** user-facing catalogue entries are the settings entries that are enabled, have an available provider and pass `DCM_ASSISTANT_ENTRY_ALLOWED`.
- **Default:** the stored default when it is enabled, otherwise the settings default rule applied to the enabled entries.
- **Disabled or removed entry:** a chat whose entry is disabled, or no longer in the catalogue, is switched to the current default (the effort is kept when the default allows it, otherwise its first effort) when its next turn starts, instead of failing with `invalid_selection`.
- **None enabled:** when at least one provider is available but the user has no enabled, allowed entry, `GET /assistant/status/` answers `{"status": "unavailable", "reason": "no_model_enabled", "providers": {...}}`, `GET /assistant/catalogue/` answers `{"default": null, "entries": []}`, and a turn start is refused with `not_configured`.
- **Admin writes** may not leave the default disabled while another entry is enabled: such a request is 400 `default_disabled`.

**Estimate.** Every catalogue entry (user and admin views) carries `average_cost_usd_micros`: the mean `cost_usd_micros` of completed turns with reported usage for that driver and model over the last 30 days, across all users, rounded to an integer. It is `null` while fewer than 10 such turns exist. The value is cached for 10 minutes. The existing `estimate` field is unchanged.

**Tool steps.** Turn JSON gains `tool_steps: [{"name", "label"}]`, the tools used by the turn in order, from the stored tool trace. Live `tool` frames are unchanged. Sources stay in `sources`.

**Endpoints** (relative to the mount prefix, authenticated):

| Method and path | Contract |
| --- | --- |
| `GET /assistant/limit/` | The current user's state: `{"period": "day"|"month", "limit_usd_micros": int, "used_usd_micros": int, "resets_at": ISO-8601}`; without a configured limit every field is `null`. `no-store`. |
| `GET /assistant/admin/limit/` | Admins only (403 `forbidden` otherwise): `{"period", "limit_usd_micros", "default_limit_usd_micros", "override": bool}`; 404 `not_configured` without `DCM_ASSISTANT_COST_LIMIT`. |
| `PATCH /assistant/admin/limit/` | Admins only: `{"limit_usd_micros": int ≥ 0 | null}` (null removes the override); answers the admin limit shape. Other keys or values are 400 `invalid_request`. Applies to the next turn. |
| `GET /assistant/admin/models/` | Admins only: `{"default": entry_id|null, "entries": [entry + {"enabled": bool, "provider_available": bool}]}` with every settings entry, including disabled ones. |
| `PATCH /assistant/admin/models/` | Admins only: `{"enabled": [entry_id, ...], "default": entry_id|""|null}` (each key optional; an empty or null default clears the stored default, so the settings rule picks among the enabled entries); unknown ids are 400 `invalid_selection`, a disabled default is 400 `default_disabled`; answers the admin models shape. |

The admin writes are recorded in the audit log.

## WebSocket

Route: `ws/assistant/chats/<chat_id>/`. The consumer inherits `BaseSecureConsumer`; unauthenticated sessions close with 4401 and owner/scope failures close with 4403. A live session is rechecked for every frame. More than `frame_rate_count` frames during `frame_rate_window` seconds closes with 4429. Archived chats accept the connection for history but reject `send` with an `archived` error.

Client frames:

```json
{"type":"send","message":"Question text","context":{"draft":"unsaved data"}}
{"type":"stop"}
```

Server frames:

```json
{"type":"history","chat_id":"uuid","turns":[turn],"summary":{"up_to_seq":3,"text":"...","created_at":"..."}}
{"type":"delta","text":"partial answer"}
{"type":"tool","phase":"start","name":"tool_name","label":"Searching"}
{"type":"tool","phase":"end","name":"tool_name","label":"Searching","sources":[{"label":"Document","reference":"ref"}]}
{"type":"turn","turn":{"id":"uuid","seq":1,"user_text":"...","assistant_text":"...","state":"completed","error_code":null,"sources":[],"driver":"openai_api","model":"...","effort":"medium","usage":{"input_tokens":1,"output_tokens":1},"billing_source":"deployment"}}
{"type":"notice","code":"history_rebuilt"}
{"type":"notice","code":"history_trimmed","left_out":2}
{"type":"notice","code":"summarised","up_to_seq":3}
{"type":"app","name":"change_proposals","payload":{}}
{"type":"error","code":"quota_exceeded"}
```

Allowed error codes: `quota_exceeded`, `provider_error`, `timeout`, `prompt_too_long`, `invalid_output`, `invalid_frame` (malformed or oversized client frame or context), `security_violation`, `not_configured`, `rate_limited`, `invalid_selection`, `archived`, `turn_active`, `cancelled`, and `unavailable` (with the app's `reason`). Provider details and stderr are never sent to clients. Tool activity uses only registered labels; tool sources are persisted in the final turn trace and flattened into final `turn.sources`. App frames are delivered to the chat owner before the final `turn` frame.

## Lifecycle and provider security

One generating turn per chat is enforced by a partial unique constraint. Heartbeats refresh at the configured interval; stale rows reconcile on chat access and with `assistant_reconcile_turns`. A user title is never overwritten by automatic first-turn naming. Provider switching and missing CLI sessions rebuild history from stored completed turns; history rebuilds trim oldest turns to `max_prompt_bytes` and emit `history_trimmed`. Claude CLI sessions rebuild when accumulated input tokens exceed `session_rebuild_tokens` or the configured prompt version changes. While a provider stream runs, scope permission, session validity, and the optional turn-validity hook are rechecked at `access_recheck_seconds`; lost access cancels the turn and closes the socket with 4403. CLI sessions live in per-chat 0700 directories; CLI stdin receives the prompt, built-in tools are disabled with `--tools ""`, and an init event must exactly match the MCP tool allow list or fail closed. Before init, system events other than init are ignored; any other event before init and any incorrect init fail closed. If a resumed CLI session fails before init, the session reference is discarded so the next turn starts fresh and rebuilds history from stored turns. Its child environment is allow-listed and excludes `ANTHROPIC_API_KEY`; stderr stays server-side. A cancelled or failed process tree is killed through the injectable kill function.
