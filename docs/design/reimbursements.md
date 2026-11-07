# Reimbursement core rules

`django_core_micha.reimbursements` provides pure reimbursement data rules for money and FX quantization,
amount distribution, template accounts, cash allocation, required fields, and cash FX override reasons.
Its public API includes `AllocationError`, the money constants and helpers, `TEMPLATE_ACCOUNTS`,
`TEMPLATE_ACCOUNT_VALUES`, `is_template_account`, the cash allocation helpers, and the validation helpers.

Host applications keep their own models, ORM, storage, and authorization. Behavior is characterized against
`reimbursements claims/models.py`. The package also exports host-neutral workbook and dossier renderers plus a
secure payment-excerpt helper.

## Artifact rendering

The package also exposes host-neutral reimbursement artifact renderers. `render_xlsx_part` reads an explicit
employer template path and renders one workbook part from plain `ExportUnit` and `ExportRow` values. It keeps
the form's existing cell mapping, page capacities, and dropdown validations. It does not discover templates,
read host models, manage export revisions, or store artifacts.

`render_dossier_pdf` creates the cover, withdrawal, reimbursement, and evidence pages from plain dossier
values. Receipt, bank proof, and substitute proof items are selected according to the payment method and cash
allocation flags. Missing and unreadable attachments receive explicit pages, and the returned stats describe
included attachments, unsupported or missing attachment payloads, missing required proof, and withdrawals.

`render_regions_excerpt` takes explicit `Region` crops from a statement PDF and returns a newly generated A4
PDF. A region's bounding box is `(x0, y0, x1, y1)` in PDF user-space points, measured from the page's
bottom-left origin. Each selected area is rasterized, then the crops are stacked vertically with a white
separator. The output contains only caller-provided context text and that raster image; source text, hidden
text, annotations, attachments, and metadata are not copied. Invalid, encrypted, or oversized input fails
closed with an `ExcerptError` code, and the helper never returns source PDF bytes.
