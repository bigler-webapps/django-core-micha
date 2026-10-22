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

**Target working directory:** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` (git root,
`main`). Use absolute paths. The checkout is shared: `src/django_core_micha/scripts/sync_secrets.py`
and `tests/test_sync_secrets.py` carry another session's uncommitted edits — do not open, format or
revert them.

### Verified facts (Orchestrator, 2026-10-22)

- `assistant/completion.py:21-77` is the whole one-shot path: prompt/size checks, catalogue
  selection, `resolve_credential`, `_make_driver` (`assistant/turns.py:216`), one `provider.stream(...)`
  inside `asyncio.timeout(provider_timeout)`, text deltas capped at `max_output_bytes`, `usage` merged,
  one `record_ai_call` with the usage, CLI session directory removed in `finally`.
- **Cost guard is inside the API drivers**, per SDK call: `openai_api.py:28-71` and
  `anthropic_api.py:128-151` reserve (`base._reserve`, denial → `DriverError("quota_exceeded")`),
  settle on success and release/charge on failure. A repair round that calls `provider.stream(...)`
  again is therefore reserved and settled like the first call — do not add a second guard in the core.
  `claude_cli` (subscription) has no cost guard today and gets none.
- `max_repair_rounds` exists in `assistant/limits.py:10` (default 2); it is used by structured chat
  turns (`turns.py:366-389`), which this WO does not touch.
- Native structured output as `extraction` uses it: OpenAI Responses
  `text={"format": {"type": "json_schema", "name": ..., "strict": True, "schema": schema}}`
  (`extraction/drivers/openai_driver.py:84-95`); Anthropic
  `output_config={"format": {"type": "json_schema", "schema": schema}}`
  (`extraction/drivers/anthropic_driver.py:95-101`). The assistant's Anthropic driver already sends
  `output_config={"effort": ...}` (`assistant/drivers/anthropic_api.py:27-29`) — merge, do not replace.
- **Claude CLI (2.1.296 installed here; no deployment pins a version)** has `--json-schema <schema>`.
  Measured with stream-json: the CLI answers through an internal tool, so the `system/init` event's
  `tools` list contains `"StructuredOutput"` in addition to the MCP tools; the assistant event carries a
  `tool_use` block named `StructuredOutput` (no text block); the `result` event carries
  `structured_output` (the parsed object) and `result` (its JSON text). The driver's init check
  (`claude_cli.py:147-157`) compares the tool list exactly and would raise `security_violation` —
  the expected set must include `"StructuredOutput"` exactly when a schema is passed, never otherwise.
- `extraction.schema_contract.validate_schema` (`extraction/schema_contract.py:10-38`) only rejects
  type arrays and objects without `additionalProperties: false`; dcm has no JSON-Schema validator
  dependency (`jsonschema` is not declared) — do not add one.

### Decisions (Orchestrator mapping of Part A; implement exactly)

1. **Signature and result.** `acomplete`/`complete` gain `response_schema=None`.
   `CompletionResult` gets a third field `data` defaulting to `None`, so `CompletionResult(text, usage)`
   still constructs and the no-schema result is unchanged.
2. **Schema gate, before credential resolution or any driver call.** A schema must pass
   `validate_schema` AND the completion subset: root `type: "object"`; only the keywords `type`,
   `properties`, `required`, `additionalProperties`, `items`, `enum`, `const`, `anyOf`, `description`,
   `title`; `type` one of `object, array, string, number, integer, boolean, null`; every object has
   `properties` and its `required` lists exactly all its property names (OpenAI strict mode requires
   that; optional values are expressed with `anyOf` + `null`). Anything else raises
   `DriverError("invalid_schema")` (new code) and no provider call happens. The subset is what the
   core validator below can enforce on all three paths — never a silent loosening.
3. **Core validator** in a new module `src/django_core_micha/assistant/structured.py`:
   `check_schema(schema)` (decision 2) and `validation_error(value, schema)` → `None` or a short
   message with a JSON path (≤ 300 chars, no schema dump). Booleans are not numbers; `integer` means a
   Python `int` that is not `bool`.
4. **Getting the object.** A driver may yield `{"type": "structured", "data": <object>}`; the core
   prefers that over the text. Otherwise the core parses the joined text: strip whitespace and at most
   one surrounding Markdown code fence (```` ``` ```` or ```` ```json ````), then `json.loads`; the
   result must be a JSON object. Then `validation_error`.
5. **Repair loop in the core.** On a parse or validation failure, up to `max_repair_rounds` further
   calls; each repair call sends the original prompt, the previous answer (the text, or the structured
   object as JSON) and a note: `Your previous answer did not match the required JSON schema: <error>.
   Answer again with only the JSON object.` API drivers receive it as `messages` (user, assistant,
   user); the CLI receives a fresh one-shot `prompt` payload whose `message` contains the same three
   parts (no `--resume`). Still invalid after the last round → `DriverError("invalid_output")`. Each call
   keeps today's per-call `provider_timeout` and `max_output_bytes`.
6. **Usage.** Sum `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`,
   `cache_tokens` over all calls; other keys (e.g. the CLI's `context_tokens`) keep the last call's
   value. `record_ai_call` runs once with the summed usage, as today.
7. **Drivers.** `stream(..., response_schema=None, ...)`:
   - `openai_api`: when given, `responses.create(..., text={"format": {"type": "json_schema",
     "name": "completion", "strict": True, "schema": schema}})`; omitted otherwise (the chat request
     stays byte-identical).
   - `anthropic_api`: when given, `output_config={"effort": ..., "format": {"type": "json_schema",
     "schema": schema}}`; otherwise unchanged.
   - `claude_cli`: when given and the CLI supports it (`--json-schema` appears in `claude --help`,
     checked once per binary path and cached, 10 s timeout, a failed check counts as unsupported), add
     `--json-schema <compact JSON>`, expect `"StructuredOutput"` in the init tool list, and yield
     `{"type": "structured", "data": ...}` from the result event's `structured_output` when it is a
     dict. When unsupported, append the schema to the prompt message with the instruction to answer
     with only a JSON object matching it, and let the core parse the text. Without a schema nothing in
     the CLI path changes.

### Files

- `src/django_core_micha/assistant/completion.py`, new `src/django_core_micha/assistant/structured.py`,
  `src/django_core_micha/assistant/drivers/openai_api.py`, `anthropic_api.py`, `claude_cli.py`.
- Tests: extend `src/django_core_micha/assistant/tests/test_completion.py` (existing fakes and
  fixtures at its top) or add `test_completion_structured.py` beside it.
- Do NOT edit `docs/`, `CHANGELOG.md`, the version, `WORK_ORDERS.md` or `webapp-management`
  (Orchestrator's).

### Required tests (Part A) — how to pin them

1. No schema: an existing-style fake driver returns exactly `CompletionResult("ok", usage, None)` and
   the drivers' SDK calls carry no `text`/`format`/`--json-schema`.
2. Per driver with a schema: OpenAI and Anthropic with their SDK clients faked at the client boundary
   but returning the SDK's own event/message types (see `test_completion_uses_api_cost_guard_for_calling_user`
   in `test_completion.py` and `tests/test_extraction.py` for how the repo builds them; follow
   [feedback: fakes must be real SDK objects] — construct `openai.types.responses` /
   `anthropic.types` objects, not dicts) — assert the request carries the native format and the
   result has `data`. CLI: a fake process factory emitting the measured event sequence (init with
   `StructuredOutput`, assistant `tool_use`, result with `structured_output`) returns `data`; argv
   contains `--json-schema`; with capability detection forced off, the prompt carries the schema and
   a text answer is parsed.
3. Repair: invalid then valid answer → second answer's `data`, usage summed over both calls, the
   second call's input carries the validation error; three invalid answers with
   `max_repair_rounds=2` → `invalid_output` after exactly three calls.
4. Invalid schemas (type array, missing `additionalProperties: false`, `$ref`, `pattern`, optional
   property not in `required`, non-object root) → `invalid_schema`, and the fake driver factory /
   credential resolver were never called.
5. Cost guard: with the real OpenAI driver and `_reserve` patched to record, an invalid-then-valid
   run reserves and settles twice; a reservation denied on the repair call raises `quota_exceeded`.
6. CLI init check: with a schema, an init tool list WITHOUT `StructuredOutput` or with an extra tool is
   `security_violation`; without a schema, `StructuredOutput` in the list is `security_violation`.

Run only the tests you wrote, from the target working directory:
`python -m pytest src/django_core_micha/assistant/tests -q -p no:cacheprovider -k "completion"`

### Invariants and pitfalls

- Chat turns, structured turns, the WebSocket protocol, `extraction` and the no-schema request bodies
  stay unchanged.
- The validation error text is generated by the core validator from the schema path; it never contains
  credentials or the full schema.
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

## Part C — Orchestrator-only

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

- Execution: `codex/gpt-6-luna` CLI, reasoning high, WO on stdin, `-C` the target directory. Launch
  evidence: (filled after dispatch).
- Review: all four lenses plus `sec_review` in one batch, read-only, diff inline.
- Verification: the assistant tests, `extraction` tests, `tests/test_declared_dependencies.py`;
  the Orchestrator writes `docs/design/assistant-chat.md`, `CHANGELOG.md`, the minor bump and the
  `webapp-management/SHARED_CAPABILITIES.md` row (that repo, `main`, own commit).

- Tier 3 (a change inside shared-core): all reviewers, full context.
- Release: tag and publish per dcm's publish-from-main topology; consumers bump via their own WOs (hram: HRAM-DEP-8 / HRAM-AI-1).
- The working tree may carry foreign uncommitted changes (`scripts/sync_secrets.py`, seen 2026-10-22); commit only own files.
