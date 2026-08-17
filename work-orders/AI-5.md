# AI-5 — Raw-file upload path for document extraction

**Target repo:** `django-core-micha` (branch `main` — this repo has no `develop`)
**Tier:** 3 — shared-core.

**No prototype.** Backend-only capability, no surface.

**Why now:** AI-2 (migrate `reimbursements` onto AI-1) discovered, while its implementation map was
being filled, that AI-1's drivers only accept an inline base64 image or already-extracted PDF text.
The app's current OpenAI code uploads the raw file instead in two cases: a PDF whose text layer is
empty (scanned/image-only PDF — the model must read it visually, or it gets no document content at
all), and an image at `medium`/`high` reasoning effort (uploaded instead of inlined, to save
vision-reasoning tokens). Neither case has anywhere to go in AI-1's normalised request today. Left
unfixed, AI-2 would either silently degrade scanned-PDF extraction (real regression on the money
path) or keep a second, dcm-shaped copy of the upload lifecycle in the app — the exact duplication
AI-1 exists to prevent. Operator decision 2026-08-17: fix this in dcm, covering both providers now
(AI-3 will move `reimbursements` to Claude and would hit the same PDF gap on the Anthropic driver
otherwise) and both attachment kinds (PDF-fallback and image-at-higher-effort), for full parity with
what the app does today.

---

## A. Envelope

### Goal & expected outcome

**Goal.** Both drivers (`openai_driver`, `anthropic_driver`) can send an attachment by uploading the
raw bytes to the provider and referencing the resulting file, as an alternative to inlining it —
selected by the caller, not decided inside dcm.

**Expected outcome.** A caller that has a scanned PDF (no extractable text) or wants to upload an
image instead of inlining it can express that on the normalised request, and the driver performs the
full lifecycle: upload, reference the file in the request, issue the request, then delete the
uploaded file afterwards — on both success and failure. Everything AI-1 already does for the inline
path (schema validation before any provider call, explicit `thinking`, cost metering after the
response, the taxonomy of errors) applies unchanged to the upload path.

### Scope

- **Extend the normalised request** (`types.py`) with whatever fields express "upload this attachment
  rather than inlining it" — exact shape is the Orchestrator's/implementer's call, not prescribed
  here. It must support both an image and a PDF being uploaded (the two cases named above), stay
  backward-compatible with every existing AI-1 caller (upload is additive — nothing about the current
  inline/pdf-text request shape changes), and make upload-vs-inline the caller's decision, not
  something dcm infers from file size or content.
- **OpenAI driver:** upload via the Files API, referencing the file in the request the same way the
  app does today (`_upload_file` in `reimbursements/backend/claims/services/openai_ocr.py`, already
  read by AI-1's Orchestrator — it tries purpose `user_data` then falls back to `assistants`),
  building an `input_file` content block from the returned file id.
- **Anthropic driver:** the equivalent upload-and-reference lifecycle for Anthropic's API. **This is
  new territory — no repo in this workspace has an `anthropic` file-upload integration to copy.**
  Verify the actual method names, purpose/lifecycle semantics, and content-block shape against the
  `anthropic` SDK version this package already depends on (added by AI-1) — do not assume they mirror
  OpenAI's.
- **Cleanup.** The uploaded file is deleted after the request completes, on both success and failure
  — matching the app's current `finally: client.files.delete(...)` with delete errors swallowed
  (best-effort cleanup must never mask the real response or the real error).
- **Ordering unchanged.** Schema validation still runs before any provider-facing call — including
  before the upload itself, so an invalid schema never causes bytes to be uploaded and then discarded.

### Non-goals / do not touch

- **No decision logic for when to upload vs inline.** That stays with the caller (in AI-2's case,
  `reimbursements`' own reasoning-effort/pdf-text-empty branching) — dcm exposes the capability, it
  does not choose when to use it.
- **No changes to cost guard, schema contract, response salvage, or the error taxonomy.** This is
  purely an additional input-handling path.
- **No streaming, multi-turn, session resume, tool use, or agent loop** — unchanged from AI-1.
- **`reimbursements` is not touched by this order.** AI-2 consumes this once published, as its own
  separate order.

### Risks

- **Anthropic's upload API is unverified.** Get the method/parameter names from the installed SDK
  (introspect it, or its docs), not from assumption or from mirroring OpenAI's shape uncritically.
- **Leaking uploaded files on the provider account.** If cleanup only runs on the success path, a
  request that fails after upload leaks a file every time. The delete must run regardless of outcome.
- **A delete failure masking the real result.** The original code swallows delete errors inside a
  bare `except Exception: pass` — preserve that; a cleanup failure must never become the thing the
  caller sees instead of their actual response or error.
- **Schema validation happening after the upload** would waste an upload (and leak it, if the request
  is then never issued) on every invalid-schema call. Keep validation first, as today.

### Required tests to WRITE

Narrow, no network — stub the provider client's file-upload/delete/request methods, same pattern as
AI-1's existing driver tests.

- Given an attachment marked for upload (image or PDF) and no inline/pdf-text content, the driver
  calls the upload endpoint before issuing the extraction request, and references the returned file
  in the request payload in the provider's expected shape.
- The uploaded file is deleted after a **successful** response.
- The uploaded file is deleted after a **failed** extraction request (the delete still happens; the
  original request failure is still what's raised).
- A failure during delete is swallowed and does not replace or mask the real response/error.
- Schema-contract validation still runs, and still raises, before any upload call is made — the
  existing "zero calls recorded on an invalid schema" test pattern, extended to assert zero upload
  calls too.
- OpenAI: the purpose fallback (`user_data` then `assistants`) is exercised — a test where the first
  purpose raises and the second succeeds.
- Every existing AI-1 driver/salvage/cost-guard/schema test continues to pass unchanged — this order
  is additive, not a rewrite of the inline path.

**Version.** Extends an existing capability area rather than adding a new one, so this is a **patch**
bump, not minor.

---

## B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

**Target working directory:** `C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root).

**Anthropic upload API, verified against the installed `anthropic==0.122.0` SDK (this workspace's
first use of it — do not assume it mirrors OpenAI):**
- Files live under `client.beta.files`, not `client.files`. `client.beta.files.upload(file=(filename,
  bytes, mime_type))` returns a `FileMetadata` with `.id`; `client.beta.files.delete(file_id)` removes
  it. The SDK auto-adds the `anthropic-beta: files-api-2025-04-14` header for these two calls — no
  explicit beta flag needed on `upload`/`delete` themselves.
- **Referencing an uploaded file in a request is different: the *stable* `client.messages.create`
  does not accept a file reference at all.** It must go through `client.beta.messages.create(...,
  betas=["files-api-2025-04-14"])` — the explicit `betas` kwarg is required there (unlike
  `beta.files.*`, it is not auto-added).
- Content-block shape for a referenced file: `{"type": "document", "source": {"type": "file",
  "file_id": ...}}` for a PDF, `{"type": "image", "source": {"type": "file", "file_id": ...}}` for an
  image — confirmed against `anthropic.types.beta.beta_file_document_source_param` /
  `beta_file_image_source_param` (`{"type": "file", "file_id": str}`).
- **Only switch to `client.beta.messages.create` when a file was actually uploaded.** The existing
  inline-image/pdf-text path keeps calling `client.messages.create` exactly as AI-1 shipped it — this
  is what keeps AI-1's existing tests (whose stub client only has `.messages`, no `.beta`) passing
  unchanged, and there is no reason to route a request that needs none of the beta surface through it.

**OpenAI upload:** unchanged shape from `_upload_file` in
`reimbursements/backend/claims/services/openai_ocr.py` (already read for AI-1) — `client.files.create
(file=(filename, bytes, mime_type), purpose=...)`, trying `"user_data"` then `"assistants"` on
failure, referenced as `{"type": "input_file", "file_id": upload.id}`. Unlike Anthropic, one block
type covers both a PDF and an image upload — no branching on MIME type needed here.

**Files to change:**
- `src/django_core_micha/extraction/types.py` — add `upload_bytes: bytes | None = None`,
  `upload_mime_type: str | None = None`, `upload_filename: str | None = None` to `ExtractionRequest`.
  Extend `__post_init__`: require `upload_mime_type` + `upload_filename` when `upload_bytes` is set
  (mirrors the existing `image_mime_type` check), and reject `image_bytes` + `upload_bytes` both set
  — the caller picks exactly one attachment mode per request.
- `src/django_core_micha/extraction/drivers/_common.py` — add a `best_effort_delete(delete_fn)`
  helper: call it, swallow any exception. Both drivers' cleanup needs this same swallow-and-never-mask
  behaviour; factor it once rather than duplicating the bare `except Exception: pass`.
- `src/django_core_micha/extraction/drivers/openai_driver.py` — add a private `_upload_file(client,
  filename, mime_type, content)` helper (the purpose-fallback loop above). In `extract`: when
  `request.upload_bytes is not None` (and `image_bytes` is not — mutually exclusive per the type),
  upload and append the `input_file` block; wrap the upload call's own exceptions as
  `DocumentExtractionError(REQUEST_FAILED, ...)` same as the existing request-call wrapping. Track the
  returned upload object in an outer-scope variable and delete it in a `finally` around everything from
  the upload attempt through the return — so cleanup runs whether the extraction request that follows
  succeeds, fails, or truncates.
- `src/django_core_micha/extraction/drivers/anthropic_driver.py` — same shape: upload via
  `client.beta.files.upload`, append the `document`/`image` file-reference block (branch on
  `upload_mime_type == "application/pdf"`), and when an upload happened, issue the request via
  `client.beta.messages.create(betas=["files-api-2025-04-14"], **call)` instead of
  `client.messages.create(**call)`. Same `finally`-based cleanup via `client.beta.files.delete`.

**Invariants restated for this map specifically:**
- Schema validation stays the first statement in both drivers, before anything upload-related —
  unchanged from AI-1.
- The existing AI-1 test suite's driver stubs (`SimpleNamespace(responses=...)` /
  `SimpleNamespace(messages=...)`) must keep working unmodified for every test that doesn't exercise
  upload — confirms the inline path truly wasn't touched.
- A delete failure in the `finally` must never propagate — it would otherwise replace a real
  successful `ExtractionResult` or a real `DocumentExtractionError` with an unrelated cleanup error.

Work from this package: open only the two driver files, `types.py`, `_common.py`, and the test file,
to verify.

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

`sec_reviewer` has two specific questions:

1. Is cleanup (file delete) unconditional — does it run on every exit path, including a failed
   extraction request — and does a delete failure ever mask the real response or error?
2. Does schema validation still run, and still block, before any upload call, on both driver paths?

### Verification

No prototype. The evidence is the test suite, run by the Orchestrator: the new upload/cleanup tests
plus the full existing AI-1 extraction suite, confirming no regression on the inline/pdf-text paths.
Confirm explicitly that no test opens a network connection.

### Register + commit

The `AI-5` row reaches `done` only with both reviewers and their verdicts named in the `Notiz`, and
the published patch version. Commit and push to `main` on green.

`reimbursements` AI-2 was paused specifically for this gap — once AI-5 is published, resume AI-2 with
Part B updated to use the new upload path for the scanned-PDF and higher-effort-image cases.

### Mini-handover

Repo: `django-core-micha` (`C:\Users\biglmi\Documents\webapps\django-core-micha`), branch `main`.
WO: `work-orders/AI-5.md`. Source material for the OpenAI shape: `_upload_file` in
`reimbursements/backend/claims/services/openai_ocr.py` (read-only reference). Blocks: `reimbursements`
AI-2 (paused on this).
`git pull`, read the WO, then follow `orchestrate-codex`.
