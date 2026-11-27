# Shared assistant chat contract

`django_core_micha.assistant` is opt-in. Add it to `INSTALLED_APPS`, mount `django_core_micha.assistant.urls` under an application-selected REST prefix, and include `django_core_micha.assistant.routing.websocket_urlpatterns` in the authenticated Channels router. The app owns prompts, read-only tools, scope authorization, catalogue entries and limit overrides. The core enforces scope access; chats are owner-only unless the app explicitly shares a non-empty scope.

## Centrally managed AI policy

Enrolled deployments also add `django_core_micha.ai_policy` and run its migrations. Set `DCM_AI_POLICY_ENABLED`, `DCM_AI_POLICY_APP_ID`, `DCM_AI_POLICY_ENVIRONMENT`, the trusted HTTPS `DCM_AI_POLICY_BASE_URL`, and a dedicated `DCM_AI_POLICY_ENROLLMENT_CREDENTIAL` through the app's deployment settings. `DCM_AI_POLICY_SOURCES` maps source IDs to a trusted driver, display label and `credential_setting` used locally to resolve a server-side model credential. A catalogue row uses `source_id` to distinguish multiple accounts offering the same model.

Run `manage.py sync_ai_policy --watch` as a deployment-managed worker to maintain contact even when no chat is open. The transport checks a fixed enrolment endpoint. It durably applies the newest valid policy for the configured app/environment before acknowledging that revision. Request-time checks continue to use the last valid policy during a transport outage. Until an enrolled deployment has a valid policy, managed provider calls are unavailable.

For the shared receipt reader, set `DCM_RECEIPTS_AI_POLICY_CATALOGUE` to the locally approved source/model rows with `driver`, `source_id`, `model` and `efforts`. `DCM_RECEIPTS_DISCLOSED_PROVIDERS` lists the providers covered by the receipt disclosure (`openai`, `anthropic`); when unset, only `DCM_RECEIPTS_PROVIDER` is eligible. Policy selects the route among those providers, and invalid settings or an undeclared route are refused before dispatch. `DCM_RECEIPTS_EFFORT` must be supported by the selected model. A missing or incompatible standard makes recognition unavailable before any provider receives the document.

In managed mode, the authenticated catalogue and availability response add `policy: {revision: integer|null}`. Family-aware entries have `{id, provider, family, label, efforts}`; IDs identify provider/family, independently of the selected source and model version. OpenAI offers Astra, Sol, Terra and Luna; Claude offers Fable, Opus, Sonnet and Haiku. The default and selection carry `entry`, provider, family and effort. A chat create or patch may submit `selection: {entry, effort}`; conflicting IDs and provider/family values are rejected. Managed user responses omit private source IDs, labels, billing modes and concrete models. A legacy policy without family mappings yields an unavailable public catalogue until a family-aware policy is applied. Unmanaged clients retain the legacy contract described below.

The trusted policy retains `allowed` and `standard` for transition and adds `families` entries with `provider`, `family`, `source_id`, `model_id`, `capabilities`, optional `efforts`, `legacy` source/model pairs and optional `code_execution` settings, plus `default_family: {provider, family}`. Each family has one assigned eligible source; duplicate or incompatible mappings and an invalid default are rejected. Existing recognized source/model selections are reconciled against the mapping; unresolved selections remain unavailable. Updates are applied durably by revision and the last valid state survives delivery failures. A running request retains its resolved route; queued requests check the current policy before dispatch. Provider failures do not switch accounts.

Direct workloads call `django_core_micha.ai_policy.resolver.resolve_workload` immediately before dispatch with a provider/family and the workload capability, supplying the workload's existing eligibility callback when needed. The returned route and credential are server-only. Continue to apply consent, access, data and cost checks around that boundary. Code execution requires the resolved source and model to have the execution capability and the existing execution policy; it is rejected before dataset upload when unavailable. For async work, run the resolver through `sync_to_async`. Unenrolled assistant apps continue to use their existing catalogue and credentials.

Managed hosted execution requires an exact source, model, driver and tool match in the centrally delivered `verified_execution` list, with an evidence reference. The trusted central service must only publish attestations backed by independent verification of the actual source/model capability; an arbitrary evidence string alone is not proof. `DCM_AI_EXECUTION_CAPABILITY_CHECK(source_id, driver, model, tool_version)` may impose a further restriction; a failing or negative check denies execution. The revisioned list permits central version updates without a consumer model list. The app's authorization and dataset-consent checks remain mandatory.

For a managed completion or extraction initiated by a user, the billing record keeps settlement pending until `DCM_AI_USAGE_SETTLEMENT_HOOK(user, record)` succeeds. The app adapter applies its existing tariff, exemptions and ledger rules and must use `record.operation_reference` as its idempotency reference. It receives normalized input, output, cache-read and cache-write counts; it must not use the upstream cost as the user's tariff. `settle_ai_usage(record.pk)` retries a reported pending operation under a database lock. Missing provider usage stays unresolved and is not sent to the adapter; a later `record_ai_call` with reported usage can settle the same operation. If the billing app is installed, a managed user request is rejected before provider dispatch when its tariff adapter is missing; apps must explicitly encode exemptions in their adapter. A failing adapter leaves settlement pending. App/system work without a user is not charged to a user. Chat replies use the existing assistant usage hook, which is likewise required before a managed user chat call.

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
- `DCM_ASSISTANT_EXECUTION_POLICY(chat, user, turn, entry) -> dict | None` is an optional trusted server hook for hosted analysis. It returns `{"enabled": True, "datasets": [...]}` only after authorizing the current private chat, requester and each dataset. Each dataset contains `name`, `data_class`, UTF-8 `content` bytes and `media_type` (CSV or JSON); client paths, URLs and provider IDs are not accepted as authorization. Anthropic additionally requires `anthropic_retention_approval` with `policy: "code_execution_up_to_30_days_non_zdr"`, `app: True` and `data_classes` covering every supplied class. Absent policy, pricing, model support or access denies execution.
- Catalogue entries opt in with `code_execution: {"enabled": True, "tool_version": "code_interpreter"|"code_execution_20250825", "memory_limit": "1g"|"4g"|"16g"|"64g", "max_execution_seconds": int, "max_continuations": int}` for their matching OpenAI or Anthropic API driver. `DCM_ASSISTANT_EXECUTION_PRICING` maps `<driver>:<model>` to `{"usd_micros": positive_int, "unit": "openai_container_20m"|"anthropic_execution_5m"}`. Prices must meet the provider's documented minimum and cover the configured account; the bounded execution allowance is reserved before provider resources are opened and conservatively recorded as estimated where actual charges are unavailable. Unknown or unpriced configurations fail closed. `claude_cli` never offers execution.
- Execution uses a fresh provider container per turn, with no outbound network; OpenAI explicitly sets the disabled network policy. The trusted policy is re-evaluated around app-tool continuations before additional inputs are transferred. The application must return the complete currently authorized snapshot and must not rely on client or model-supplied file references; a missing or changed previously uploaded dataset fails closed. OpenAI adds inputs to the current container and Anthropic sends `container_upload` blocks on a continuation. Both paths retain private-scope access, provider retention approval and cleanup requirements.
- Bounded CSV, JSON and plain-text outputs from the current analysis are retained as inert data on the private turn in `execution_results`, under the existing chat authorization checks. A truncated execution record includes `truncation` metadata, and a truncated file includes `truncated`, `omitted_bytes` and `truncation_reason`; consumers should visibly report the cut. An oversized JSON file may be omitted rather than returned as invalid partial JSON. Limits in `DCM_ASSISTANT_LIMITS` include `max_execution_files`, `max_execution_file_bytes`, `max_execution_total_bytes`, `max_execution_output_files`, `max_execution_output_file_bytes`, `max_execution_output_total_bytes`, `max_execution_text_bytes`, `max_execution_seconds` and `max_execution_continuations`. Provider cleanup is attempted on completion, cancellation and failure. Anthropic container data can persist for up to 30 days, is not Zero Data Retention eligible, and generated Files API objects require deletion; provider expiration is not proof of immediate deletion.
- The initial verified capability set is `openai_api:gpt-5` and `anthropic_api:claude-haiku-4-5`; other model IDs are denied until independently verified. The execution amount is a conservative estimate for the bounded duration, not a claim that the provider reported a currency charge; current provider usage may report request counts without the billable duration or account-specific final charge. Update the supported set and price floors only with current provider evidence.
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
# Private chat group grants (integration contract)

An app may register `DCM_ASSISTANT_GROUP_CATALOGUE(chat, user)` returning groups with unique IDs, labels, `read` or `write` kind and members. Members have unique IDs, labels, a boolean `available`, optional `tools` and `selectors` lists, and an `unavailable_reason` when unavailable. Write members declare `confirmation` as `direct` or `confirmation_required`.

A private chat stores enabled group IDs, or null for all read groups. Unknown IDs are ignored. Each tool offer, invocation and selector access intersects the enabled groups with the current catalogue's available members. Availability remains an app authorization upper bound. A newly added member or restored right becomes available when its group is enabled. Changes apply to the next offer or invocation; running answers and existing history are unchanged. Shared chats and apps without a catalogue retain their existing behavior.

`GET assistant/chats/<uuid>/group-grants/` returns `supported`, `grant_revision`, `catalogue_revision` and `groups`, including group `enabled` and `editable` and member `effective`. An unconfigured app returns `{"supported": false}`. Only the owner of a private, non-archived chat may access this endpoint.

`PUT` accepts `group_ids`, `grant_revision`, `catalogue_revision`, and optional `reauthorize_group_ids`. The latter two are accepted for client compatibility and ignored. A save replaces the enabled set; a stale grant revision returns HTTP 409 with `{"code": "stale_grants", "error": "stale_grants"}`. Archived chats return the existing archived conflict and shared or non-owner chats return 404. `TURN_CONTEXT` hooks receiving private chat data should accept `allowed_selectors` and restrict access accordingly.
