# DCM-RMB-3 — Optional account in reimbursement workbook rows

## Part A — Envelope

This is a bounded continuation of the approved `bigler-consult/work-orders/BCO-EXP-3.md` Part A
sections 5–6. Tier 3: the shared package changes. The BCO export must carry the selected exact
employer category into the appropriate workbook row. The existing shared renderer clears column K
and has no account input. Support an optional per-row account, preserving existing consumers' blank
column K when no account is supplied. Preserve all 17 exact template values and the workbook's
dropdowns. Arbitrary text must not become an executable spreadsheet formula.

Scope: the shared reimbursement export row and renderer; tests of category mapping on both sheets,
omitted values and malicious formula-like text. Existing reimbursement consumers, layout, monetary
mapping, dossier rendering and all unrelated package capabilities stay unchanged. No dependencies,
CI, authorization or schema changes. Risks: a wrong column/row or formula injection in a shared
financial artifact; behavioral changes for legacy consumers.

## Part B — Implementation map

Target working directory: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

- `src/django_core_micha/reimbursements/template_export.py`: `ExportRow` currently has six required
  positional fields. Extend it compatibly with an optional account; `render_xlsx_part` clears K for
  all 49 slots, maps 14 rows to `Expenses  Page 1` 28–41 and 35 rows to `Expenses Page 2` 13–47,
  restores `Codes!$A$3:$A$19` dropdowns and forces user-written formula strings to literal text.
  An explicitly supplied category must be an exact `TEMPLATE_ACCOUNT_VALUES` value; reject invalid
  values with `TemplateExportError`. Preserve blank behavior when omitted.
- `tests/test_reimbursements_render.py`: extend the existing synthetic workbook tests. Assert a
  known account in K28 and K13, old six-argument `ExportRow` remains valid with K blank, and invalid
  and formula-looking accounts cannot be emitted as formulas. Existing styles and formulas remain.
- `src/django_core_micha/reimbursements/accounts.py` holds the canonical 17 values. Do not edit it.
  Foreign edits to `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` are outside scope.

### Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, relevant role skill and `MEMORY.md` ONLY
> for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI. Do NOT edit
> `WORK_ORDERS.md` or `MEMORY.md`. Your tools are only for editing source and test files and running
> the tests you wrote. Do not install dependencies, touch lockfiles, use a package manager, tidy
> files, or run git. Leave changes uncommitted. Write the required tests and run just those tests
> once; the orchestrator independently reviews and runs the authoritative set. Use UTF-8. Begin
> with `PLAN:`, emit `PROGRESS: [n/total]` before actions and finish exactly `RESULT: DONE` or
> `RESULT: BLOCKED` with a concise explanation.

## Part C — Orchestrator only

STOP — the following is not for the implementer.

Route implementation to configured `codex/gpt-6-luna`. Review independently using the configured
Tier-3 lenses, test the real template and prior six-argument consumer compatibility. Preserve
foreign work. Record the release and integration in the register and BCO work order. Publish an
additive patch release after green; upgrade BCO only after the actual artifact is available. The
existing reimbursements app's blank-account behavior is intentionally preserved until a separate
consumer change is approved and reviewed within BCO-EXP-3.
