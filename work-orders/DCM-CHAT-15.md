# DCM-CHAT-15 — Structured one-shot completion: `complete(..., response_schema=)`

## Part A — Envelope

### Goal and expected outcome

`django_core_micha.assistant.completion.complete()` / `acomplete()` can return a schema-valid JSON object, so apps can move their schema-typed one-shot calls from `extraction` (built for documents) onto the assistant's catalogue, credential resolver, timeout, cost guard and usage recording. First consumer: hram's Application (HRAM-AI-1).

After this order:

- `complete(user, *, prompt, system="", entry=None, driver=None, model=None, effort=None, purpose="completion", response_schema=None)`.
- Without `response_schema` the behaviour and result are unchanged (`CompletionResult(text, usage)`).
- With `response_schema` the result additionally carries `data`: the parsed JSON object, validated against the schema. The schema contract is the one `extraction` already validates (`extraction.schema_contract.validate_schema`); an invalid schema fails before any provider call.
- Every driver returns schema-valid data or raises a typed `DriverError`:
  - `openai_api` and `anthropic_api` use the provider's native structured output, the same mechanism the `extraction` drivers use.
  - `claude_cli` uses the CLI's structured-output option where the pinned CLI offers one; otherwise the schema is given in the prompt and the answer is parsed and validated.
- If the answer is not valid JSON or does not validate, the core makes a repair call carrying the validation error, up to `max_repair_rounds` (existing limit, default 2). After that it raises `DriverError("invalid_output")`.
- Usage and cost cover every call of the attempt (initial plus repairs) and are reported once in the result. Each call goes through the cost guard; a denied reservation is `quota_exceeded` as today.

### Decisions taken (operator, 2026-10-22, hram Application requirements round)

- The schema enforcement lives in dcm, not in the consuming app, so every app gets it.
- `claude_cli` must be supported: hram uses the operator's Claude subscription for local and staging.

### Scope

1. `assistant/completion.py`: the `response_schema` parameter, parsing, validation, repair loop, aggregated usage.
2. Driver support for native structured output (`openai_api`, `anthropic_api`) and the `claude_cli` path described above.
3. `docs/design/assistant-chat.md` (the completion paragraph), `CHANGELOG.md`, a minor version bump, and the `SHARED_CAPABILITIES.md` row for the AI chat core (one-shot structured completion).

### Non-goals

- Chat turns, the WebSocket protocol and `DCM_ASSISTANT_STRUCTURED_TURNS`: unchanged.
- `extraction`: unchanged and still supported.
- No new drivers, no per-user credentials, no streaming of partial structured output.

### Risks

- Native structured output differs per provider; the schema subset accepted by `validate_schema` must be expressible on all three paths, otherwise the schema is refused before the call (never a silent loosening).
- Repair rounds multiply cost; they count against the cost guard like any call.
- The CLI's structured-output option depends on the pinned CLI version; the fallback path must produce the same result contract.

### Required tests to write

1. Without `response_schema`: result and usage unchanged (regression).
2. With a schema, per driver (fakes built from the real SDK objects, not hand-made dicts): a valid answer returns `data`; the provider request carries the native structured-output setting for `openai_api` / `anthropic_api`.
3. Repair: an invalid first answer and a valid second one return the second, with usage summed over both calls; invalid answers beyond `max_repair_rounds` raise `invalid_output`.
4. An invalid schema is refused before any provider call.
5. Cost guard: each repair call reserves and settles; a denied reservation is `quota_exceeded`.

## Part B — Implementation map — addressed to the implementer

**Not dispatch-ready.** The Orchestrator fills the verified context package before dispatch.

**Target working directory:** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

### Named orientation files

- `src/django_core_micha/assistant/completion.py`
- `src/django_core_micha/assistant/drivers/openai_api.py`, `anthropic_api.py`, `claude_cli.py`, `base.py`
- `src/django_core_micha/extraction/schema_contract.py`, `extraction/drivers/openai_driver.py`, `extraction/drivers/anthropic_driver.py` (existing native structured-output usage)
- `src/django_core_micha/assistant/limits.py`
- `docs/design/assistant-chat.md`

### Preamble

> Part A is the complete scope. Do not edit WORK_ORDERS.md; do not install dependencies, change lockfiles or tidy foreign changes. Do not git add/commit/push, self-review or treat your own run as the gate. Stop and report an out-of-scope cause. The Orchestrator supplies the progress contract and runtime-specific preamble.

## Part C — Orchestrator-only

**STOP — this section is for the Orchestrator, not a dispatched implementer.**

- Tier 3 (a change inside shared-core): all reviewers, full context.
- Release: tag and publish per dcm's publish-from-main topology; consumers bump via their own WOs (hram: HRAM-DEP-8 / HRAM-AI-1).
- The working tree may carry foreign uncommitted changes (`scripts/sync_secrets.py`, seen 2026-10-22); commit only own files.
