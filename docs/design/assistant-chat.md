# Shared assistant chat contract

`django_core_micha.assistant` is opt-in. Add it to `INSTALLED_APPS`, mount `django_core_micha.assistant.urls` under an application-selected REST prefix, and include `django_core_micha.assistant.routing.websocket_urlpatterns` in the authenticated Channels router. The app owns prompts, read-only tools, scope authorization, catalogue entries and limit overrides. The core always enforces chat ownership.

## App hooks and settings

- `DCM_ASSISTANT_SYSTEM_PROMPT_BUILDER(chat) -> str`
- `DCM_ASSISTANT_TOOL_REGISTRY -> iterable[ToolSpec]`; each tool has `name`, `label`, `description`, `input_schema`, and `handler(user, chat, arguments, trust_class) -> (content, sources)`. `sources` is a list of `{label, reference}` records. v1 trust class is always `cloud`.
- `DCM_ASSISTANT_SCOPE_PERMISSION(user, scope) -> bool`; empty scope is allowed by default.
- `DCM_ASSISTANT_CATALOGUE`: list of `{driver, model, name, efforts, billing, default}` where driver is `claude_cli`, `openai_api`, or `anthropic_api`; effort is from `low|medium|high|max`; billing is `subscription|pay_per_use`; exactly one entry has `default: true`.
- `DCM_ASSISTANT_LIMITS`: optional mapping overriding `frame_rate_window`, `frame_rate_count`, `max_concurrent_turns`, `max_turns_per_user`, `max_message_length`, `max_prompt_bytes`, `max_output_bytes`, `provider_timeout`, `idle_timeout`, `heartbeat_seconds`, `stale_turn_seconds`, and `max_tool_calls`.
- Deployment credential settings: `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`. Each turn invokes `resolve_credential(user, driver)` once. Per-user credentials are deferred. API calls reserve and settle through extraction's cost guard; reservation denial is `quota_exceeded`.

## REST

All paths below are relative to the mount prefix. Every endpoint requires authentication. Chat reads, writes, turns, and deletes are owner-only and scope-checked. Missing and foreign chats both return 404.

| Method and path | Contract |
| --- | --- |
| `GET /assistant/catalogue/` | `{ "default": {"entry": entry_id, "effort": effort}|null, "entries": [entry] }`. Entry: `{id, driver, model, label, efforts, billing, default}` where `id` is `"<driver>:<model>"` (stable, used as the picker key) and `label` is the catalogue display name. |
| `GET /assistant/status/` | `{ "status": "available"|"not_configured", "providers": { "claude_cli": "available"|"not_configured", "openai_api": ..., "anthropic_api": ... } }`; `status` is `available` when any provider is usable. |
| `GET /assistant/chats/?cursor=&limit=&archived=false` | `{ "results": [chat], "next": string|null, "has_more": bool }` |
| `POST /assistant/chats/` | JSON `{title?, scope?, driver?, model?, effort?}`; returns chat, 201. Omitted selection uses catalogue default and its first allowed effort. |
| `GET/PATCH/DELETE /assistant/chats/{chat_id}/` | PATCH accepts title, archived boolean, and/or a selection, either `{"selection": {"entry": entry_id, "effort": effort}}` (the surface form) or `driver`/`model`/`effort`; an entry outside the catalogue or an effort it does not allow returns 400 `invalid_selection`. Restore an archived chat with `{"archived":false}`. Other archived writes return 409 `archived`. DELETE returns 204 and removes the CLI session directory. |
| `GET /assistant/chats/{chat_id}/turns/?cursor=&limit=` | `{ "results": [turn], "next": string|null, "has_more": bool }`, oldest-to-newest within each page. |

Chat JSON fields: `id`, `scope`, `title`, `title_source`, `archived_at`, `driver`, `model`, `effort`, `selection` (`{entry, effort}` or null when the stored driver/model is no longer in the catalogue), `created_at`, `updated_at`. Turn JSON fields: `id`, `seq`, `user_text`, `assistant_text`, `state`, `error_code`, `sources`, `driver`, `model`, `effort`, `usage`, `billing_source`. Timestamps are ISO-8601 UTC strings. Usage has `input_tokens` and `output_tokens` when reported.

Cursors are opaque Django-signed values with a one-hour lifetime. Their signed body binds the cursor to the endpoint kind, owner, chat (for turn pages), archive filter and page size. Clients must pass the returned cursor unchanged; malformed, expired, tampered or cross-query cursors return HTTP 400 `{ "code":"invalid_cursor", "error":"invalid_cursor" }`.

REST errors use `{ "code": code, "error": code }`. Selection errors return 400 `invalid_selection`; archived writes return 409 `archived`; foreign or missing resources return 404.

## WebSocket

Route: `ws/assistant/chats/<chat_id>/`. The consumer inherits `BaseSecureConsumer`; unauthenticated sessions close with 4401 and owner/scope failures close with 4403. A live session is rechecked for every frame. More than `frame_rate_count` frames during `frame_rate_window` seconds closes with 4429. Archived chats accept the connection for history but reject `send` with an `archived` error.

Client frames:

```json
{"type":"send","message":"Question text"}
{"type":"stop"}
```

Server frames:

```json
{"type":"history","chat_id":"uuid","turns":[turn]}
{"type":"delta","text":"partial answer"}
{"type":"tool","phase":"start","name":"tool_name","label":"Searching"}
{"type":"tool","phase":"end","name":"tool_name","label":"Searching","sources":[{"label":"Document","reference":"ref"}]}
{"type":"turn","turn":{"id":"uuid","seq":1,"user_text":"...","assistant_text":"...","state":"completed","error_code":null,"sources":[],"driver":"openai_api","model":"...","effort":"medium","usage":{"input_tokens":1,"output_tokens":1},"billing_source":"deployment"}}
{"type":"notice","code":"history_rebuilt"}
{"type":"error","code":"quota_exceeded"}
```

Allowed error codes: `quota_exceeded`, `provider_error`, `timeout`, `prompt_too_long`, `invalid_output`, `invalid_frame` (malformed or oversized client frame), `security_violation`, `not_configured`, `rate_limited`, `invalid_selection`, `archived`, `turn_active`, and `cancelled`. Provider details and stderr are never sent to clients. Tool activity uses only registered labels; tool sources are persisted in the final turn trace and flattened into final `turn.sources`.

## Lifecycle and provider security

One generating turn per chat is enforced by a partial unique constraint. Heartbeats refresh at the configured interval; stale rows reconcile on chat access and with `assistant_reconcile_turns`. A user title is never overwritten by automatic first-turn naming. Provider switching and missing CLI sessions rebuild history from stored completed turns. CLI sessions live in per-chat 0700 directories; CLI stdin receives the prompt, built-in tools are disabled with `--tools ""`, and an init event must exactly match the MCP tool allow list or fail closed. Its child environment is allow-listed and excludes `ANTHROPIC_API_KEY`; stderr stays server-side. A cancelled or failed process tree is killed through the injectable kill function.
