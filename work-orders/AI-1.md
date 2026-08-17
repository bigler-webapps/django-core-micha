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

> **Placeholder — not yet filled.** The Orchestrator completes this section on `git pull`, within
> the envelope above: named files with `path:line`, the architecture slice, key snippets of the
> reimbursements source being lifted, invariants and pitfalls, the absolute target working
> directory, and the progress contract. **This work order must not be dispatched while this
> placeholder stands.**

Source material to lift from (read-only reference for the Orchestrator, in `reimbursements`):
`backend/claims/services/cost_guard.py` in full; and from `backend/claims/services/openai_ocr.py`
the helpers `_resize_image_bytes`, `_encode_image_inline`, `_extract_pdf_text`, `_upload_file`,
`_extract_json_candidate`, `_iter_json_objects_from_text`, `_extract_codeblock_candidates`,
`_coerce_payload_dict`, `_parse_json_payload`, `_extract_payload_from_response_obj`,
`_make_json_safe`, `_response_to_data`, `_response_to_text`, `_usage_to_metadata`, and the
`OpenAIOCRExtractionError` shape. Everything else in that file is domain and stays put.

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
