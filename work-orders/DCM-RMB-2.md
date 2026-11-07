# DCM-RMB-2 — Shared reimbursement renderers and secure payment-excerpt helper

Pointer: canonical Envelope is `bigler-consult/work-orders/BCO-EXP-3.md` (Part A sections 1, 4, 6; Gate #1
approved by the operator 2026-11-07; the operator approved `pypdfium2` as the PDF rasterizer on 2026-11-07).
Slice 2 of the shared-core work, after DCM-RMB-1 (already in `src/django_core_micha/reimbursements/`).
Tier 3 (shared-core, private financial documents).

## Part A — Envelope

Goal: add to `django_core_micha.reimbursements` (1) a host-neutral renderer for the reimbursement-form XLSX
and the evidence dossier PDF, extracted from `reimbursements/backend/exports/workflows/template_export.py`
with IDENTICAL layout and behavior, and (2) a fail-closed helper that derives a clean payment excerpt PDF
from a region of a bank-statement PDF without carrying any other statement content.

Non-goals: no Django models/ORM/storage/authorization, no host export lifecycle (batches, revisions,
status changes), no statement parsing or transaction extraction, no change to DCM-RMB-1 modules, no version
bump, no release, no dependency edits (the orchestrator already added the `reimbursements` extra to
`pyproject.toml`: openpyxl, reportlab, pypdf, Pillow, pypdfium2; they are installed locally).

Risks: layout drift in the employer form (cell mapping, page limits); leaking unrelated private statement
content through a "cropped" PDF; hidden text, annotations, embedded files or metadata surviving.

## Part B — Implementation map (addressed to the implementer)

Working directory: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Read-only source of truth:
`C:/Users/Micha Bigler/Documents/webapps/reimbursements/backend/exports/workflows/template_export.py`
(`TemplateExportWorkflow`: constants, `_write_sheet_metadata`, `_expense_slots`, `_clear_expense_rows`,
`_write_reimbursements`, `_restore_template_dropdowns`, `_generate_xlsx_bytes`, `_generate_pdf_bytes`,
`_build_text_page`, the A4 fitting and attachment-to-PDF helpers, `_artifact_filename_base`, `_xlsx_filename`,
`_pdf_filename`, `_chunk`). Do not edit anything in the reimbursements repo. Foreign uncommitted edits exist
in `src/django_core_micha/scripts/sync_secrets.py` and `tests/test_sync_secrets.py`: do not touch them.
Reuse `money.py` from DCM-RMB-1 where the source quantizes or converts.

Add to `src/django_core_micha/reimbursements/` (no Django imports; heavy libraries are imported lazily
inside the functions so `import django_core_micha.reimbursements` still works without the extra):

1. `template_export.py`
   - Constants copied from the source: `TEMPLATE_FILE_NAME`, `MAX_ROWS_PER_XLSX=49`, `PAGE_1_EXPENSE_ROWS`
     (28-41), `PAGE_2_EXPENSE_ROWS` (13-47), `ATTACHMENT_PAGE_MARGIN`, `TEMPLATE_LIST_VALIDATIONS`.
   - `class TemplateExportError(Exception)`.
   - Frozen dataclasses with plain values: `ExportUnit` (project_name, project_code, mission_name_default,
     mission_start_default: date|None, mission_end_default: date|None, mission_location_default,
     mission_country_default, claimant_first_name, claimant_last_name, claimant_display_name,
     beneficiary_address, beneficiary_city, bank_account_holder, bank_name, bank_address, bank_zip_city,
     bank_account_number, bank_iban, bank_bic, payout_currency, cost_center_code, revision_number: int,
     generated_on: date, generated_at_label: str) and `ExportRow` (expense_date: date|None, merchant_name,
     description, original_currency, original_amount: Decimal, fx_rate: Decimal).
   - `split_beneficiary_name`, `sanitize_filename` (slug via a small local slugify so Django is not needed:
     lowercase ASCII letters/digits/hyphens, empty -> `"export"`), `artifact_filename_base(unit)`,
     `xlsx_filename(unit, part_number, total_parts)`, `pdf_filename(unit)`, `chunk_rows(rows, size)`.
   - `render_xlsx_part(template_path, unit, rows) -> bytes`: load the template with openpyxl (same warning
     filter), write the metadata cells and the rows exactly as the source does (same cells: C6..C14, G12,
     G13, G14, I7, G8=None, C53..C59, per-row B,C,E,F,G,H and K cleared), clear unused slots, restore the
     three list validations, return the saved bytes. More rows than slots -> `TemplateExportError` with the
     same message shape as the source.
   - Dossier: frozen dataclasses `EvidenceItem` (reimbursement_key, proof_type in {"receipt","bank_proof",
     "substitute_proof"}, filename, mime_type, content: bytes|None, label), `DossierWithdrawal` (key,
     summary_line, evidence_reimbursement_keys), `DossierEntry` (key, section_lines: list[str],
     payment_method, has_cash_allocation: bool, evidence: list[EvidenceItem]). `render_dossier_pdf(cover_title,
     cover_lines, withdrawals, entries, *, bank_proof_required_methods=("card",))` returns
     `(pdf_bytes, stats)` with stats keys `included_attachment_count`, `unsupported_attachment_count`,
     `missing_required_count`, `withdrawal_count`. Page order and rules identical to `_generate_pdf_bytes`:
     cover page, "Cash Withdrawals" page when there are withdrawals, per withdrawal a proof page plus its
     substitute proofs, then per entry a section page, receipt-like proofs (receipts, plus substitute proofs
     when the entry has no cash allocation), "Missing Receipt/Substitute" page when none, and for methods in
     `bank_proof_required_methods` the bank proofs or a "Missing Bank Proof" page (counted in
     `missing_required_count`). Other methods never get a missing-bank-proof page and never count as missing.
     Attachments render onto A4 (PDF pages scaled and centred, images via ImageOps.exif_transpose), an
     unreadable one produces the "Unsupported Attachment" page and counts as unsupported; `content=None`
     produces the "Attachment Missing" page. Text pages as in `_build_text_page` (Helvetica, wrap at 110).
2. `excerpt.py` (fail closed, never returns source bytes)
   - `class ExcerptError(Exception)` with `code` (`encrypted`, `invalid_pdf`, `invalid_region`, `too_large`,
     `render_failed`).
   - `@dataclass(frozen=True) class Region: page_index: int; bbox: tuple[float, float, float, float]` where
     bbox is `(x0, y0, x1, y1)` in PDF user-space points, origin bottom-left, `x0<x1`, `y0<y1`; document this.
   - `render_regions_excerpt(pdf_bytes, regions, *, context_lines=(), title="Payment excerpt", scale=3.0,
     max_pixels=16_000_000) -> bytes`: open the PDF with pypdfium2 (password-protected or unparsable ->
     ExcerptError), validate every region (page exists, bbox inside the page box with a small tolerance,
     positive area, regions limited to 12, total pixels <= `max_pixels`), render each page at `scale`, crop
     the region with Pillow, stack the crops vertically (white separator) in the given order, and write a NEW
     A4 PDF with reportlab that contains only: the `context_lines` (plain text, escaped, wrapped) and the
     stacked image. The result must carry no text, objects, annotations, attachments or metadata from the
     source: set only a generic Title/empty Author/Creator "django-core-micha reimbursements"; do not copy
     anything from the source document into the output. Any exception while rendering becomes
     `ExcerptError("render_failed")`; there is no fallback that returns the original or any page of it.
   - `extract_pdf_text(pdf_bytes) -> str` small helper for verification (pypdf, all pages joined).
3. `__init__.py`: export the new public names; keep the module docstring accurate.
4. Docs: extend `docs/design/reimbursements.md` with the renderer/excerpt contracts, the bbox convention and
   the rule that the excerpt is a rasterized region (so hidden text cannot survive).

Tests (`tests/test_reimbursements_render.py`; build every fixture in code, no real documents, no binary
fixtures): a minimal synthetic template workbook created with openpyxl having sheets `"Expenses  Page 1"`,
`"Expenses Page 2"` and `"Codes"` with the cells the renderer writes, then assert exact cell values for a
two-row export, row clearing, capacity error at 14+35 rows, the restored validations, chunking and filenames
(slug rules, part suffix). Dossier: assert page counts and order for card and cash cases with synthetic PDF
and PNG evidence generated with reportlab/Pillow (page text via pypdf), the missing receipt/bank-proof pages
and `missing_required_count`, `bank_proof_required_methods=()` never adds a missing-bank page, an
unreadable attachment, a `None` attachment. Excerpt: generate a synthetic multi-page statement PDF with
reportlab containing several transactions with unique marker strings (including one on page 2 and a hidden
white-on-white marker plus a PDF annotation and an embedded-file-like attachment if reportlab/pypdf can add
one), crop one transaction and assert: the excerpt's `extract_pdf_text` contains none of the other markers and
none of the hidden ones, the raw excerpt bytes contain none of them, no `/Annot`, `/EmbeddedFile`, `/JS`
or source `/Title` in the bytes, the image is not blank (pixel variance above a threshold), a two-region
(page-boundary) excerpt stacks both regions, a bbox outside the page, an empty regions list, more than 12
regions, an oversize request, a non-PDF and an encrypted PDF each raise `ExcerptError` with the right code and
return nothing. Use concrete expected values.

Invariants: Decimal only for money; no float money math; no file I/O except the explicit `template_path`
read; do not log or print document content; keep module-level imports light (lazy imports for openpyxl,
reportlab, pypdf, PIL, pypdfium2); do not change existing files except `__init__.py` and
`docs/design/reimbursements.md`.

### Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and `MEMORY.md` ONLY for conventions. Stay in
> scope; do not touch auth/permissions/deps/schema/CI unless the spec says so. **Do NOT edit
> `WORK_ORDERS.md`.** **Your tools are for editing source and test files and for running the tests you
> wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, run any git
> command, or tidy up stray files; if something blocks you, stop and report `RESULT: BLOCKED <reason>`.
> Leave every change uncommitted. WRITE the tests and RUN only the tests you wrote once; that is not the gate.
> Use explicit UTF-8 for file I/O. Narrate with `PLAN: ...` then `PROGRESS: [n/total] ...` lines (no silent
> gap over ~2 min) and exactly one final `RESULT: DONE|BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — if you are the implementer, everything below is not for you.

- Gate #1 and the `pypdfium2` approval: operator in chat 2026-11-07.
- Execution: codex/gpt-6-luna, cwd = django-core-micha, WO on stdin. Review: envelope, regression,
  duplication, tests and security lenses (private documents) on the assembled diff; read the generated PDFs
  and xlsx myself. Commit only own paths (not the foreign sync_secrets files); dcm `main`; no version bump here.
