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

> **Placeholder — not yet filled.** The Orchestrator completes this section on `git pull`, within
> the envelope above: the exact field/type additions, named files with `path:line`, the Anthropic
> upload API surface verified against the installed SDK, invariants and pitfalls, the absolute target
> working directory, and the progress contract. **This work order must not be dispatched while this
> placeholder stands.**

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
