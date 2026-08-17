# AI-1 — Provider-agnostic document extraction capability

**Target repo:** `django-core-micha` (branch `main` — this repo has no `develop`)
**Tier:** 3 — shared-core, and it absorbs a security control (the S193 per-user daily spend cap).

**No prototype.** This work order has no surface; it is a backend capability.

**Why now:** `reimbursements` is the first consumer of LLM document extraction and `jg-ferien` is
about to be the second (programme-sheet import, PUB-3 follow-up). Two consumers is the point at
which the capability belongs centrally rather than being copied. Operator decision 2026-08-17.

---

## A. Envelope

### Goal & expected outcome

**Goal.** A shared, provider-agnostic capability for one-shot document extraction — an image or PDF
goes in, a structured payload comes out, and the call is metered against a per-user daily spend cap.

**Expected outcome.** A consuming app supplies only what is genuinely its own: the JSON schema, the
prompts, and the field normalisation. Everything between the uploaded bytes and the raw model
response — input preparation, the request, payload recovery, error taxonomy, cost metering — comes
from `django_core_micha`. Adding a third consumer is then a schema plus a prompt, not another copy
of 400 lines.

**The seam, stated once so it is not re-litigated per file:** everything that `reimbursements` and
`jg-ferien` would *agree* about is shared; everything they would *disagree* about stays in the app.
The sharpest illustration is a real conflict, not a hypothetical: reimbursements' most-repeated
prompt rule is *"never infer, calculate, or estimate — only extract values that are explicitly
printed"*, because a derived amount on a financial claim is a false statement. jg-ferien needs the
opposite: it must derive event start times from a standing rule printed on the programme sheet. A
shared prompt library would therefore be the one mistake this module must not make.

### Scope

**1. Cost guard.** Move `claims/services/cost_guard.py` (215 lines) from `reimbursements` into dcm,
generalised:

- Setting names lose the `OPENAI_` prefix; the module is no longer provider-specific.
- The rate table gains rows for the Claude models actually used. **Enter list price, not
  introductory price.** Claude Sonnet 5 is $3 / $15 per 1M — i.e. `0.3` / `1.5` cents per 1k. Its
  introductory rate of $2 / $10 expires **2026-08-31**; a table carrying the introductory figure
  under-estimates by a third from September, and a spend cap that under-estimates does not cap.
- The conservative unknown-model fallback (`1.5` / `6.0` cents per 1k) is retained unchanged — it is
  what stops a model override from walking around the cap.
- The atomic `cache.add`-then-`incr` seeding, the charge-after-response ordering, and the
  over-by-one acceptance documented in its docstring are all behaviour to preserve, not to improve.

**2. Input preparation.** Provider-free, no domain knowledge:

- Image resize to a configurable long edge (current reimbursements value: 1500 px), RGB conversion,
  JPEG re-encode at quality 85 with `optimize`.
- Base64 inline image block.
- PDF text extraction via `pypdf` with a configurable character cap (current value: 12000), tolerant
  of an unparseable file.

**3. Response salvage.** The JSON-recovery layer, which exists because structured output is not in
practice guaranteed: fenced-code stripping, brace-matching, incremental `raw_decode` scanning,
wrapper-key unwrapping (`payload` / `data` / `result` / `fields`), recursive search through a
response object including tool-call `arguments` strings, and JSON-safe coercion.

**4. Error taxonomy.** One exception carrying `code`, `message`, and `status_code`, with a fixed set
of codes every consumer needs: missing credential, missing model, missing dependency, empty file,
image conversion failed, request failed, output truncated, invalid JSON, empty payload. Codes are
**provider-neutral in their wording** — they surface to app frontends as i18n keys, and a key
containing a vendor name outlives the vendor.

**5. The schema contract — the part most likely to be got wrong.** A JSON schema cannot simply be
forwarded to both providers. Anthropic's structured outputs require `additionalProperties: false` on
**every** object and do not accept JSON-Schema type arrays; reimbursements' four current schemas use
`"additionalProperties": True` throughout and `{"type": ["string", "number", "null"]}` in every
field. The shared contract is therefore **the intersection, and the intersection is Anthropic's
rules**: schemas authored to those constraints are accepted by both providers, and the reverse does
not hold. The module validates a supplied schema against the contract **before** issuing a request,
so a violation names the rule instead of arriving as an opaque provider 400.

**6. Two thin drivers behind one normalised call.** The normalised request carries: system prompt,
user prompt, optional image bytes plus MIME type, optional extracted PDF text, schema, max output
tokens, effort level, and an explicit thinking setting. The normalised result carries: raw text, raw
response object, and token usage. Each driver places these where its provider expects them —
`reasoning.effort` versus `output_config.effort`, `max_output_tokens` versus `max_tokens`, the
respective image block shapes, the respective files lifecycles.

Two rules bind the drivers:

- **A driver must not flatten provider capability.** Anything that does not map onto the normalised
  request stays reachable through a per-provider passthrough rather than being silently dropped.
- **`thinking` is always explicit, never defaulted.** Claude Sonnet 5 runs adaptive thinking when the
  parameter is omitted, and `max_tokens` bounds thinking and answer text *together*. A one-shot
  extraction into a fixed schema gains nothing from thinking and can lose its payload to it.

### Non-goals / do not touch

- **No prompts, no schemas, no field normalisers.** Amount, currency, FX-rate and payment-method
  normalisation, `FIELD_ALIASES`, `ALLOWED_FIELDS` and the account enum stay in `reimbursements`.
  They are the feature, not the plumbing.
- **Do not modify `reimbursements` in this work order.** Its migration is AI-2 and is deliberately
  separate; see the ordering note below.
- **Do not touch `survey_app`.** Its assistant is a hardened Claude Code CLI subprocess on a
  subscription token — a different capability with a different threat model, and not a consumer here.
- **No streaming, no multi-turn, no session resume, no tool use, no agent loop.** One request, one
  response. Every one of those is a separate capability with its own failure modes.
- **No new credential handling.** Keys are read from settings as today; this work order neither
  creates nor reads `.env`.

### Ordering — and the failure mode it prevents

AI-1 has no precondition; it is the head of the chain. What it does have is an obligation
**after** it: `reimbursements` must migrate onto the module (AI-2). If the module lands and
reimbursements keeps its copy, the estate has three copies of this logic instead of two, and the
work order has made the problem worse than it found it. AI-2 is therefore not optional follow-up —
it is the second half of this change and must be tracked as such.

The chain, for context only (each has its own order):

| # | Repo | Content |
|---|---|---|
| AI-1 | `django-core-micha` | this work order |
| AI-2 | `reimbursements` | migrate onto the module, **still OpenAI**, behaviour-neutral |
| AI-3 | `reimbursements` | switch the driver to Claude, rewrite schemas to the contract, validate accuracy |
| AI-4 | `jg-ferien` | programme-sheet import as the second consumer |

AI-2 and AI-3 are split on purpose. Bundling a refactor with a provider switch on the money path
puts two unrelated risk profiles in one diff, and the security-relevant half disappears inside the
mechanical half — the same mistake this estate has made before.

### Risks

- **Pulling domain logic across the seam.** The failure is silent and only shows up at the second
  consumer, as a shared function that needs a flag to serve both. The test for every candidate is
  the one stated under Goal: would reimbursements and jg-ferien agree about it?
- **The cost guard is a security control, not a utility.** A regression in the charge path removes a
  spending limit without any visible symptom. Its existing test cases must move with it and keep
  passing; this is a relocation, not a rewrite.
- **Wrong or missing rate rows under-protect.** With no Claude row the conservative fallback applies
  at roughly three times Sonnet 5's real rate, so the cap trips about three times too early —
  the mirror-image failure of the introductory-price trap above. Both directions are wrong; the
  table must be correct, not merely safe-looking.
- **A too-permissive schema contract defers the failure to runtime.** If the module validates
  nothing, a schema that OpenAI accepts and Anthropic rejects passes review and fails in production
  on the day the driver is switched.
- **`reimbursements` has no tests for the code being moved.** `openai_ocr.py` is 1435 lines and no
  test file references it; only `cost_guard.py` has coverage. The parts being moved are pure
  functions, so this is cheap to fix — but it must be fixed *here*, in this work order's test suite,
  because AI-2 will use exactly these tests as its safety net.

### Required tests to WRITE

Narrow, and none of them may make a network call — the whole shared layer is pure functions, which
is precisely why this seam is the right one.

**Input preparation**
- An image larger than the configured long edge is downscaled to it; a smaller one is not upscaled.
- A non-RGB image (e.g. palette or RGBA) is converted; the output decodes as JPEG.
- Undecodable bytes raise the taxonomy error with the image-conversion code — not a bare `OSError`.
- A text PDF yields its text; text beyond the cap is truncated **to** the cap.
- An unparseable PDF yields an empty string rather than raising, so the caller can fall back to the
  image path.

**Response salvage** — the highest-value cases, one per real-world shape observed in the source:
- A bare JSON object.
- A fenced ```json block, and a fenced block with no language tag.
- A JSON object surrounded by prose.
- A wrapper key (`payload`, `data`, `result`, `fields`).
- A tool-call `arguments` string containing JSON.
- A list whose first dict is the payload.
- Nothing parseable → returns empty, so the caller raises the empty-payload error rather than
  writing an empty record.

**Cost guard**
- The migrated cases from `reimbursements/backend/claims/test_cost_guard.py` pass unchanged.
- A Claude model present in the table is estimated at its own rate, not the fallback.
- An unknown model still falls back conservatively (the model-override defence).
- The cap is disabled in local and enabled elsewhere, as today.

**Schema contract**
- A schema with `additionalProperties: true` on any object is rejected **before** a request is
  issued, with a message naming the rule.
- A schema using a JSON-Schema type array is likewise rejected.
- A schema meeting the contract is accepted.

**Drivers** (stub transport, no network)
- The effort level lands in `reasoning.effort` for the OpenAI driver and `output_config.effort` for
  the Anthropic driver, from one normalised input.
- The max-output-tokens value lands in each provider's own field name.
- The image block matches each provider's expected shape.
- `thinking` is present explicitly in every Anthropic request — the regression guard for the
  adaptive-thinking default.
- A provider error is re-raised as the taxonomy error with the provider's status code preserved.

**Version.** This adds a genuinely new capability area rather than extending an existing one, so it
is a **minor** bump, not a patch.

---

## B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

**Target working directory:** `C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root —
this is a plain Python package, `src/django_core_micha/`, not a running Django project).

**Source material to lift from (read-only reference, in `reimbursements`, do not edit that repo):**
`backend/claims/services/cost_guard.py` in full (214 lines); and from
`backend/claims/services/openai_ocr.py` the pure-function helpers `_resize_image_bytes`,
`_encode_image_inline`, `_extract_pdf_text`, `_extract_json_candidate`,
`_iter_json_objects_from_text`, `_extract_codeblock_candidates`, `_coerce_payload_dict`,
`_parse_json_payload`, `_extract_payload_from_response_obj`, `_make_json_safe`,
`_response_to_data`, `_response_to_text`, `_usage_to_metadata`, and the `OpenAIOCRExtractionError`
shape (code/message/status_code). Migrated test cases: all of
`backend/claims/test_cost_guard.py` (216 lines). **`_upload_file` stays behind** — it is the
OpenAI Files-API upload path, a driver-specific transport detail, not a shared primitive; the
Anthropic driver does not upload files the same way, and neither AI-1 nor AI-2 need it (AI-2
still calls the existing `openai_ocr.py` for the file-upload path unchanged — only the *pure*
helpers above are being lifted). Everything else in `openai_ocr.py` (prompts, schemas,
`ALLOWED_FIELDS`, `FIELD_ALIASES`, `_normalize_*`, `_validate_and_filter_payload`, the three
`extract_*`/`match_pairs_with_llm` entry points) is domain and stays in `reimbursements` for AI-2.

**Existing dcm module-layout convention** (verified against `src/django_core_micha/fields/`,
`.../health/`, `.../validators/`): a plain-function utility module needs no `apps.py` and no
`INSTALLED_APPS` entry — only add one if the module later needs models/migrations, which this one
does not. Follow the same pattern: a new top-level package, e.g.
`src/django_core_micha/extraction/`, with plain modules inside (suggested split below — the exact
file/function split is your call, this is a map of the seams, not a spec of the API):

- `cost_guard.py` — the moved-and-generalised S193 cap. **Behaviour is a relocation, not a
  rewrite**: the atomic `cache.add`-then-`incr` seeding, the charge-after-response ordering, the
  over-by-one acceptance, and the ≥1¢ floor must be byte-for-byte the same logic. What *does*
  change: setting names lose the `OPENAI_` prefix (`OPENAI_COST_LIMIT_ENABLED` →
  `AI_COST_LIMIT_ENABLED`, `OPENAI_DAILY_COST_LIMIT_CENTS` → `AI_DAILY_COST_LIMIT_CENTS`,
  `OPENAI_MODEL_COST_TABLE_CENTS_PER_1K` → `AI_MODEL_COST_TABLE_CENTS_PER_1K`); the cache-key
  prefix `f"{env}:openai:cost:..."` loses `openai:` → `f"{env}:ai:cost:..."`; and the cost table
  (currently OpenAI-only, see full content read above) gains Claude Sonnet 5 at **list price**
  `{"input": 0.3, "output": 1.5}` — not the introductory `$2/$10` rate, which expires
  2026-08-31 (see Scope item 1 in the Envelope). Keep the conservative unknown-model fallback
  (`1.5` / `6.0` cents/1k) unchanged — verbatim, not re-derived. `IS_LOCAL` / `ENV_TYPE` are
  already generic dcm-wide settings and need no renaming.
- `input_prep.py` — `_resize_image_bytes` and `_extract_pdf_text`, generalised: the long-edge
  (currently hardcoded `1500`) and JPEG quality (`85`) become parameters with those values as
  defaults; the PDF char cap (currently `12000`) likewise. Keep `_encode_image_inline`'s
  base64-encode step but do **not** keep its OpenAI-shaped return value
  (`{"type": "input_image", "image_url": "data:...;base64,..."}`) as the shared function's
  contract — that dict shape is OpenAI's `input_image` block, not Anthropic's (Anthropic's image
  block is `{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}`).
  Return the provider-neutral pair (resized bytes/mime, or a base64 string + mime) and let each
  driver build its own block shape — this is the concrete instance of the "driver must not
  flatten provider capability" rule in Scope item 6, applied to the *input* side.
- `salvage.py` — `_extract_json_candidate`, `_iter_json_objects_from_text`,
  `_extract_codeblock_candidates`, `_coerce_payload_dict`, `_parse_json_payload`,
  `_extract_payload_from_response_obj`, `_make_json_safe`, combined into one entry point (e.g.
  `extract_json_payload(raw_text, raw_response_obj=None) -> dict`). **The one real trap here:**
  `_extract_payload_from_response_obj`'s `_looks_like_extracted_payload` gate is keyed off
  `KNOWN_FIELD_KEYS`, which is derived from reimbursements' `ALLOWED_FIELDS` and `FIELD_ALIASES`
  — i.e. a domain-specific field-name allowlist buried inside an otherwise generic
  recovery routine. That allowlist must **not** travel into dcm (it is exactly the "would
  reimbursements and jg-ferien agree about this?" test from the Envelope's Goal section, and the
  answer is no — jg-ferien's fields are entirely different). Replace the gate with a
  domain-neutral heuristic (e.g.: a dict with no nested dict/list values, and no wrapper key
  matched, is itself a payload candidate) so the function needs no caller-supplied field list.
  Confirm this still passes the "tool-call arguments string" and "list whose first dict is the
  payload" required-test cases — neither of those needs domain knowledge, only the
  already-generic wrapper-key/recursion logic.
- `errors.py` — one exception, `code`/`message`/`status_code`, per Scope item 4. Codes are
  provider-neutral strings scoped to this package, e.g.
  `django_core_micha.extraction.<code>` (not `claims.validation.openai_*` — that prefix is
  app- and provider-specific and is exactly what item 4 says to drop). Fixed code set: missing
  credential, missing model, missing dependency, empty file, image conversion failed, request
  failed, output truncated, invalid JSON, empty payload.
- `schema_contract.py` — `validate_schema(schema: dict) -> None`, raising the taxonomy error
  (naming the violated rule) when any object node's `additionalProperties` is missing or not
  `False`, or any node's `"type"` is a JSON-Schema array. **Must recurse** into `properties` and
  `items` — the reimbursements schemas nest an object (`bbox_norm` in the bulk-extraction schema)
  and Anthropic's `additionalProperties: false` requirement applies to every object, not just the
  top level. Run it against the actual current reimbursements schemas
  (`_json_schema_format`, `_bulk_json_schema_format`, `_classify_schema_format`,
  `_match_schema_format` in `openai_ocr.py`, all `additionalProperties: True` today) as an
  informal sanity check while writing the required tests — they are the real shape this will see
  in AI-3.
- `types.py` (or similar) — the normalised request/result carried by Scope item 6: system prompt,
  user prompt, optional image bytes + MIME, optional extracted PDF text, schema, max output
  tokens, effort level, and **explicit** `thinking` (never defaulted — see Scope item 6's second
  rule; Claude Sonnet 5 runs adaptive thinking when the parameter is omitted and `max_tokens`
  bounds thinking + answer together). Normalised result: raw text, raw response object, token
  usage (`input_tokens`/`output_tokens`/`total_tokens` — note both OpenAI's and Anthropic's SDKs
  already expose `usage.input_tokens`/`usage.output_tokens` under those same attribute names, so
  `cost_guard`'s usage-extraction can stay provider-agnostic without per-provider branching).
- `drivers/openai_driver.py`, `drivers/anthropic_driver.py` — one function each taking the
  normalised request and provider credentials/model, returning the normalised result. Effort
  lands in `reasoning.effort` (OpenAI) vs `output_config.effort` (Anthropic, per Scope item 6 —
  verify the exact Anthropic parameter name against the installed `anthropic` SDK version rather
  than assuming, since this is new-to-the-estate territory: no other repo in this workspace
  currently declares an `anthropic` dependency). `thinking` must be present explicitly in every
  Anthropic request body (the regression guard named in Required tests). Wrap the provider SDK
  import in a try/except raising the `missing_dependency` taxonomy error (mirrors
  `openai_ocr.py`'s existing `except Exception: raise OpenAIOCRExtractionError(...)` around
  `from openai import OpenAI`). Provider errors re-raise as the taxonomy error with
  `status_code` preserved from the SDK exception where available, else `502` (mirrors
  `openai_ocr.py`'s `getattr(exc, "status_code", 502)` pattern). A per-provider passthrough
  kwarg (e.g. `**extra`) merged into the raw API call keeps Scope item 6's "must not flatten
  provider capability" rule concrete rather than aspirational.

**Dependencies — new to this package, must be declared in TWO places or
`tests/test_declared_dependencies.py::test_runtime_imports_are_declared_dependencies` fails:**
1. `pyproject.toml` → `[project] dependencies`: add `pypdf>=6.13.1` and `openai>=2.41.0` (both
   already pinned at these floors in `reimbursements/backend/requirements.txt` — match them, don't
   invent new floors) plus `anthropic` (no existing pin anywhere in this workspace to match —
   check the current PyPI release and use an unpinned or lower-bounded entry consistent with how
   this file already treats several deps, e.g. `PyYAML`, `django-cors-headers`, with no version
   constraint at all).
2. `tests/test_declared_dependencies.py` → `MODULE_TO_DISTRIBUTION` dict: add `"pypdf": "pypdf"`,
   `"openai": "openai"`, `"anthropic": "anthropic"`. The AST-based scanner in that test walks
   *all* function bodies including code inside `try/except ImportError` guards — declaring the
   dependency is required even though the import is defensive at runtime.

**Version:** bump `pyproject.toml`'s `version` from `2.41.2` to `2.42.0` (minor — new capability
area, per the Envelope's own version note).

**Invariants / do-not-touch, restated from the Envelope for this section specifically:**
- No prompts, schemas, or field normalisers cross the seam (Non-goals).
- Do not touch `reimbursements` or `survey_app` in this WO (Non-goals) — the source-material reads
  above are read-only reference.
- No streaming/multi-turn/tool-use/agent-loop surface on the drivers (Non-goals).
- No new credential handling — drivers read `api_key`/model from whatever the caller passes in
  (mirroring how `cost_guard`/`openai_ocr.py` read from Django `settings` today); this WO does not
  touch `.env` or `secrets.yaml`.

Work from this package: open only the files named above (in `reimbursements`, read-only) plus
whatever you create under `src/django_core_micha/extraction/` and the two dependency-declaration
files, to verify.

## Preamble — a REQUIRED block IN this file, not something appended at invocation

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`,
> and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch
> auth/permissions/deps/schema/CI unless the spec says so; do not update `MEMORY.md`. **Do NOT edit
> `WORK_ORDERS.md` — the register row and the review verdicts are the orchestrator's alone.** Do NOT
> `git add`/`commit`/`push` — leave every change uncommitted in the working tree for the
> orchestrator's independent review. WRITE the tests the `Required tests` section calls for AND
> **RUN the tests you just wrote** to confirm they execute and pass — that is the ONLY test run you
> do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the authoritative
> set + does the independent review after you finish — those are the gate; your own run does not
> count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

---

## C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the invocation described
> below; do NOT shell out to `codex exec`.

### Execution directive

Check `.claude/codex-status.md` in the workspace root before invoking. With no line for the day of
execution, use Codex — directly via Bash, never through the `debugger`/`*_coder` wrappers, with BOTH
flags `--skip-git-repo-check` and `--dangerously-bypass-approvals-and-sandbox`. Falling back to
direct implementation flips authorship and makes an independent `reviewer` mandatory.

### Review routing

Tier 3, shared-core: **`reviewer` and `sec_reviewer`**, concurrently in one background batch before
the commit. No `ui_reviewer` — there is no surface.

`sec_reviewer` has two specific questions, not a general pass:

1. Does the migrated spend cap still cap? Specifically: is the charge still issued after the
   response, is the seed-or-increment still atomic, does an unknown model still fall back
   conservatively, and can a consumer-supplied model or rate table lower the effective limit?
2. Does the schema-contract validator actually run before the request is issued on **both** driver
   paths, or only on one?

### Verification

No prototype, no two-width comparison. The evidence is the test suite, run by the Orchestrator: the
new dcm module tests plus the migrated cost-guard cases. Confirm explicitly that no test opens a
network connection — the whole point of this seam is that the shared layer is testable without a
provider.

### Register + commit

The `AI-1` row reaches `done` only with both reviewers and their verdicts named in the `Notiz`, and
with the published minor version. Commit and push to `main` on green.

AI-2 must be registered in `reimbursements` before AI-1 is closed, so the obligation stated under
Ordering survives the end of this work order rather than depending on someone remembering it.

### Mini-handover

Repo: `django-core-micha` (`C:\Users\biglmi\Documents\webapps\django-core-micha`), branch `main`.
WO: `work-orders/AI-1.md`. Source material to lift: `reimbursements/backend/claims/services/`.
Consumers: `reimbursements` (AI-2/AI-3), `jg-ferien` (AI-4, PUB-3 follow-up).
`git pull`, read the WO, then follow `orchestrate-codex`.
