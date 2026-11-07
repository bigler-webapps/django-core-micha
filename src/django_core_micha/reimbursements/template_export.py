"""Host-neutral renderers for reimbursement forms and evidence dossiers."""

from __future__ import annotations

import io
import re
import textwrap
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Literal, Sequence


TEMPLATE_FILE_NAME = "Reimbursement_Form_Template.xlsx"
MAX_ROWS_PER_XLSX = 49
PAGE_1_EXPENSE_ROWS = list(range(28, 42))
PAGE_2_EXPENSE_ROWS = list(range(13, 48))
ATTACHMENT_PAGE_MARGIN = 24
TEMPLATE_LIST_VALIDATIONS = (
    ("Expenses  Page 1", "G12", "=Codes!$A$23:$A$26"),
    ("Expenses  Page 1", "K28:K41", "=Codes!$A$3:$A$19"),
    ("Expenses Page 2", "K13:K47", "=Codes!$A$3:$A$19"),
)


class TemplateExportError(Exception):
    """Raised when a reimbursement artifact cannot be rendered."""


@dataclass(frozen=True)
class ExportUnit:
    project_name: str
    project_code: str
    mission_name_default: str
    mission_start_default: date | None
    mission_end_default: date | None
    mission_location_default: str
    mission_country_default: str
    claimant_first_name: str
    claimant_last_name: str
    claimant_display_name: str
    beneficiary_address: str
    beneficiary_city: str
    bank_account_holder: str
    bank_name: str
    bank_address: str
    bank_zip_city: str
    bank_account_number: str
    bank_iban: str
    bank_bic: str
    payout_currency: str
    cost_center_code: str
    revision_number: int
    generated_on: date
    generated_at_label: str


@dataclass(frozen=True)
class ExportRow:
    expense_date: date | None
    merchant_name: str
    description: str
    original_currency: str
    original_amount: Decimal
    fx_rate: Decimal


def split_beneficiary_name(full_name: str | None) -> tuple[str, str]:
    """Split a beneficiary name into all preceding names and the final surname."""
    cleaned = str(full_name or "").strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split()
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value)
    return slug.strip("-")


def sanitize_filename(value: str | None) -> str:
    """Return an ASCII filename slug, using ``export`` for an empty value."""
    return _slugify(str(value or "")) or "export"


def artifact_filename_base(unit: ExportUnit) -> str:
    parts = (
        sanitize_filename(unit.project_name or unit.project_code),
        sanitize_filename(unit.claimant_first_name),
        sanitize_filename(unit.claimant_last_name),
        sanitize_filename(unit.cost_center_code),
    )
    filtered = [part for part in parts if part and part != "export"]
    return "_".join(filtered or ["export"])


def xlsx_filename(unit: ExportUnit, part_number: int, total_parts: int) -> str:
    filename = artifact_filename_base(unit)
    if total_parts > 1:
        filename = f"{filename}_part_{part_number}"
    return f"{filename}.xlsx"


def pdf_filename(unit: ExportUnit) -> str:
    return f"{artifact_filename_base(unit)}_dossier.pdf"


def chunk_rows(rows: Sequence[ExportRow], size: int) -> Iterable[list[ExportRow]]:
    """Yield list chunks in input order, matching the host export workflow."""
    for index in range(0, len(rows), size):
        yield list(rows[index:index + size])


def render_xlsx_part(template_path: str | Path, unit: ExportUnit, rows: Sequence[ExportRow]) -> bytes:
    """Render one reimbursement-form workbook part from a supplied template."""
    from openpyxl import load_workbook
    from openpyxl.worksheet.datavalidation import DataValidation

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Data Validation extension is not supported and will be removed",
            category=UserWarning,
        )
        workbook = load_workbook(template_path)

    ws_page_1 = workbook["Expenses  Page 1"]
    ws_page_1["C6"] = unit.project_name or unit.project_code or f"REV-{unit.revision_number}"
    ws_page_1["C7"] = unit.claimant_first_name
    ws_page_1["C8"] = unit.claimant_last_name
    ws_page_1["C9"] = unit.beneficiary_address or ""
    ws_page_1["C10"] = unit.beneficiary_city or ""
    ws_page_1["C11"] = unit.project_name or unit.mission_name_default or ""
    ws_page_1["C12"] = unit.cost_center_code or ""
    ws_page_1["G12"] = str(unit.payout_currency or "CHF").upper()
    ws_page_1["C13"] = unit.mission_start_default.isoformat() if unit.mission_start_default else ""
    ws_page_1["G13"] = unit.mission_end_default.isoformat() if unit.mission_end_default else ""
    ws_page_1["C14"] = unit.mission_location_default or ""
    ws_page_1["G14"] = unit.mission_country_default or ""
    ws_page_1["G8"] = None
    ws_page_1["I7"] = unit.generated_on.isoformat()
    ws_page_1["C53"] = unit.bank_account_holder or unit.claimant_display_name or ""
    ws_page_1["C54"] = unit.bank_name or ""
    ws_page_1["C55"] = unit.bank_address or ""
    ws_page_1["C56"] = unit.bank_zip_city or ""
    ws_page_1["C57"] = unit.bank_account_number or ""
    ws_page_1["C58"] = unit.bank_iban or ""
    ws_page_1["C59"] = unit.bank_bic or ""

    ws_page_2 = workbook["Expenses Page 2"]
    slots = [(ws_page_1, row) for row in PAGE_1_EXPENSE_ROWS]
    slots.extend((ws_page_2, row) for row in PAGE_2_EXPENSE_ROWS)
    if len(rows) > len(slots):
        raise TemplateExportError(
            f"Too many reimbursements for one workbook part: {len(rows)} > {len(slots)}."
        )

    for worksheet, row in slots:
        for column in ("B", "C", "E", "F", "G", "H", "K"):
            worksheet[f"{column}{row}"] = None

    for reimbursement, (worksheet, row) in zip(rows, slots):
        description = " - ".join(
            value
            for value in (reimbursement.merchant_name, reimbursement.description)
            if str(value or "").strip()
        )
        worksheet[f"B{row}"] = reimbursement.expense_date.isoformat() if reimbursement.expense_date else ""
        worksheet[f"C{row}"] = description
        worksheet[f"E{row}"] = None
        worksheet[f"F{row}"] = reimbursement.original_currency or ""
        worksheet[f"G{row}"] = reimbursement.original_amount
        worksheet[f"H{row}"] = reimbursement.fx_rate

    # Every value written above is user data. openpyxl turns a string that starts with "=" into a formula,
    # so such a value would run when finance opens the file (for example a HYPERLINK in a merchant name).
    # Force the cells this function wrote back to literal text; formulas that belong to the template stay.
    written = [(ws_page_1, ref) for ref in (
        "C6", "C7", "C8", "C9", "C10", "C11", "C12", "G12", "C13", "G13", "C14", "G14", "I7",
        "C53", "C54", "C55", "C56", "C57", "C58", "C59",
    )]
    written.extend((worksheet, f"{column}{row}") for worksheet, row in slots for column in ("B", "C", "F", "G", "H"))
    for worksheet, ref in written:
        cell = worksheet[ref]
        if isinstance(cell.value, str) and cell.data_type == "f":
            cell.data_type = "s"

    for sheet_name, cell_range, formula in TEMPLATE_LIST_VALIDATIONS:
        validation = DataValidation(type="list", formula1=formula, allow_blank=True)
        workbook[sheet_name].add_data_validation(validation)
        validation.add(cell_range)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


ProofType = Literal["receipt", "bank_proof", "substitute_proof"]
ReimbursementKey = str | int


@dataclass(frozen=True)
class EvidenceItem:
    reimbursement_key: ReimbursementKey
    proof_type: ProofType
    filename: str
    mime_type: str
    content: bytes | None
    label: str


@dataclass(frozen=True)
class DossierWithdrawal:
    key: ReimbursementKey
    summary_line: str
    evidence_reimbursement_keys: list[ReimbursementKey]


@dataclass(frozen=True)
class DossierEntry:
    key: ReimbursementKey
    section_lines: list[str]
    payment_method: str
    has_cash_allocation: bool
    evidence: list[EvidenceItem]


def _build_text_page(title: str, lines: Sequence[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, 800, title)
    y = 775
    pdf.setFont("Helvetica", 10)
    for line in lines:
        for segment in textwrap.wrap(str(line), width=110) or [""]:
            if y < 70:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
                y = 800
            pdf.drawString(40, y, segment)
            y -= 14
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _fit_to_a4(width: float, height: float) -> tuple[float, float, float, float, float]:
    from reportlab.lib.pagesizes import A4

    a4_width, a4_height = A4
    available_width = a4_width - ATTACHMENT_PAGE_MARGIN * 2
    available_height = a4_height - ATTACHMENT_PAGE_MARGIN * 2
    scale = min(available_width / width, available_height / height)
    fitted_width = width * scale
    fitted_height = height * scale
    x = (a4_width - fitted_width) / 2
    y = (a4_height - fitted_height) / 2
    return fitted_width, fitted_height, x, y, scale


def _pdf_attachment_pages(content: bytes) -> list[bytes]:
    from pypdf import PdfReader, PdfWriter, Transformation
    from reportlab.lib.pagesizes import A4

    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise ValueError("Encrypted attachment PDF")
    output: list[bytes] = []
    for page in reader.pages:
        if getattr(page, "rotation", 0):
            page.transfer_rotation_to_content()
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        _, _, x, y, scale = _fit_to_a4(width, height)
        writer = PdfWriter()
        target = writer.add_blank_page(width=A4[0], height=A4[1])
        target.merge_transformed_page(
            page,
            Transformation().scale(scale, scale).translate(tx=x, ty=y),
        )
        buffer = io.BytesIO()
        writer.write(buffer)
        output.append(buffer.getvalue())
    if not output:
        raise ValueError("Attachment PDF contains no pages")
    return output


def _image_attachment_page(content: bytes) -> bytes:
    from PIL import Image, ImageOps
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    with Image.open(io.BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        width, height, x, y, _ = _fit_to_a4(*image.size)
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        pdf.drawImage(
            ImageReader(image), x, y, width=width, height=height,
            preserveAspectRatio=True, anchor="c",
        )
        pdf.showPage()
        pdf.save()
        return output.getvalue()


def _attachment_pages(item: EvidenceItem) -> tuple[list[bytes], bool]:
    if item.content is None:
        return [
            _build_text_page(
                "Attachment Missing",
                [f"Attachment {item.label or item.filename or 'unknown'} has no file payload."],
            )
        ], False

    try:
        if item.filename.lower().endswith(".pdf") or item.mime_type == "application/pdf":
            return _pdf_attachment_pages(item.content), True
        return [_image_attachment_page(item.content)], True
    except Exception:
        return [
            _build_text_page(
                "Unsupported Attachment",
                [
                    f"Attachment {item.label or item.filename or 'unknown'} "
                    "could not be rendered into the dossier PDF."
                ],
            )
        ], False


def render_dossier_pdf(
    cover_title: str,
    cover_lines: Sequence[str],
    withdrawals: Sequence[DossierWithdrawal],
    entries: Sequence[DossierEntry],
    *,
    bank_proof_required_methods: Sequence[str] = ("card",),
) -> tuple[bytes, dict[str, int]]:
    """Render a cover, withdrawal proofs, and reimbursement evidence in order."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    stats = {
        "included_attachment_count": 0,
        "unsupported_attachment_count": 0,
        "missing_required_count": 0,
        "withdrawal_count": len(withdrawals),
    }

    def append_pdf(content: bytes) -> None:
        reader = PdfReader(io.BytesIO(content))
        for page in reader.pages:
            writer.add_page(page)

    def append_item(item: EvidenceItem) -> None:
        pages, included = _attachment_pages(item)
        stats["included_attachment_count"] += int(included)
        stats["unsupported_attachment_count"] += int(not included)
        for page_pdf in pages:
            append_pdf(page_pdf)

    append_pdf(_build_text_page(cover_title, cover_lines))

    if withdrawals:
        append_pdf(_build_text_page("Cash Withdrawals", [item.summary_line for item in withdrawals]))

    evidence_by_key: dict[ReimbursementKey, list[EvidenceItem]] = {}
    for entry in entries:
        evidence_by_key.setdefault(entry.key, []).extend(entry.evidence)

    def key_order(key: ReimbursementKey) -> tuple[int, object]:
        return (0, key) if isinstance(key, int) else (1, str(key))

    for withdrawal in withdrawals:
        linked_keys = sorted(set(withdrawal.evidence_reimbursement_keys), key=key_order)
        proof_items = [
            evidence
            for key in linked_keys
            for evidence in evidence_by_key.get(key, ())
            if evidence.proof_type == "substitute_proof"
        ]
        if not proof_items:
            continue
        append_pdf(
            _build_text_page(
                f"Withdrawal #{withdrawal.key} Proof",
                [
                    "Attached proof(s) linked via reimbursement IDs: "
                    + ", ".join(str(key) for key in linked_keys)
                ],
            )
        )
        for item in proof_items:
            append_item(item)

    required_methods = set(bank_proof_required_methods)
    for entry in entries:
        append_pdf(_build_text_page("Reimbursement Section", entry.section_lines))
        receipt_like = [
            item for item in entry.evidence
            if item.proof_type == "receipt"
            or (item.proof_type == "substitute_proof" and not entry.has_cash_allocation)
        ]
        bank_proofs = [item for item in entry.evidence if item.proof_type == "bank_proof"]

        if not receipt_like:
            stats["missing_required_count"] += 1
            append_pdf(
                _build_text_page(
                    "Missing Receipt/Substitute",
                    [f"No receipt/substitute proof found for reimbursement #{entry.key}."],
                )
            )
        for item in receipt_like:
            append_item(item)

        if entry.payment_method in required_methods:
            if not bank_proofs:
                stats["missing_required_count"] += 1
                append_pdf(
                    _build_text_page(
                        "Missing Bank Proof",
                        [f"No bank proof found for card reimbursement #{entry.key}."],
                    )
                )
            for item in bank_proofs:
                append_item(item)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue(), stats
