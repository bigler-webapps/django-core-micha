from __future__ import annotations

import io
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _unit():
    from django_core_micha.reimbursements import ExportUnit

    return ExportUnit(
        project_name="Zürich Field Work",
        project_code="ZH-9",
        mission_name_default="Mission default",
        mission_start_default=date(2026, 2, 3),
        mission_end_default=date(2026, 2, 9),
        mission_location_default="Bern",
        mission_country_default="CH",
        claimant_first_name="Amy Marie",
        claimant_last_name="Smith",
        claimant_display_name="Amy Marie Smith",
        beneficiary_address="1 Main Street",
        beneficiary_city="Basel",
        bank_account_holder="Amy M Smith",
        bank_name="Example Bank",
        bank_address="2 Bank Road",
        bank_zip_city="4000 Basel",
        bank_account_number="1234567",
        bank_iban="CH9300762011623852957",
        bank_bic="EXAMPLECH",
        payout_currency="eur",
        cost_center_code="CC 42",
        revision_number=7,
        generated_on=date(2026, 11, 7),
        generated_at_label="07.11.2026 14:30",
    )


def _rows(count=2):
    from django_core_micha.reimbursements import ExportRow

    sample = [
        ExportRow(date(2026, 10, 1), "Cafe Lune", "Team lunch", "EUR", Decimal("12.35"), Decimal("0.94125000")),
        ExportRow(date(2026, 10, 2), "Train", "", "CHF", Decimal("48.00"), Decimal("1.00000000")),
    ]
    return [sample[index % 2] for index in range(count)]


def _template_file(tmp_path):
    from openpyxl import Workbook
    from django_core_micha.reimbursements import PAGE_1_EXPENSE_ROWS, PAGE_2_EXPENSE_ROWS

    workbook = Workbook()
    page_1 = workbook.active
    page_1.title = "Expenses  Page 1"
    page_2 = workbook.create_sheet("Expenses Page 2")
    workbook.create_sheet("Codes")
    for row in PAGE_1_EXPENSE_ROWS:
        for column in ("B", "C", "E", "F", "G", "H", "K"):
            page_1[f"{column}{row}"] = "TEMPLATE SENTINEL"
    for row in PAGE_2_EXPENSE_ROWS:
        for column in ("B", "C", "E", "F", "G", "H", "K"):
            page_2[f"{column}{row}"] = "TEMPLATE SENTINEL"
    page_1["G8"] = "OLD VALUE"
    path = tmp_path / "minimal-template.xlsx"
    workbook.save(path)
    return path


def _page_texts(pdf_bytes):
    from pypdf import PdfReader

    return [(page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf_bytes)).pages]


def _pdf_with_text(text):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.drawString(60, 700, text)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def _png_evidence():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (120, 80), "white")
    ImageDraw.Draw(image).rectangle((15, 10, 105, 70), fill=(32, 90, 180))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_xlsx_metadata_rows_clearing_and_validations(tmp_path):
    from openpyxl import load_workbook
    from django_core_micha.reimbursements import render_xlsx_part

    path = _template_file(tmp_path)
    result = render_xlsx_part(path, _unit(), _rows())
    workbook = load_workbook(io.BytesIO(result))
    page_1 = workbook["Expenses  Page 1"]

    expected_metadata = {
        "C6": "Zürich Field Work",
        "C7": "Amy Marie",
        "C8": "Smith",
        "C9": "1 Main Street",
        "C10": "Basel",
        "C11": "Zürich Field Work",
        "C12": "CC 42",
        "G12": "EUR",
        "C13": "2026-02-03",
        "G13": "2026-02-09",
        "C14": "Bern",
        "G14": "CH",
        "G8": None,
        "I7": "2026-11-07",
        "C53": "Amy M Smith",
        "C54": "Example Bank",
        "C55": "2 Bank Road",
        "C56": "4000 Basel",
        "C57": "1234567",
        "C58": "CH9300762011623852957",
        "C59": "EXAMPLECH",
    }
    for cell, value in expected_metadata.items():
        assert page_1[cell].value == value

    assert [page_1[f"{column}28"].value for column in ("B", "C", "E", "F", "G", "H", "K")] == [
        "2026-10-01", "Cafe Lune - Team lunch", None, "EUR", 12.35, 0.94125, None,
    ]
    assert [page_1[f"{column}29"].value for column in ("B", "C", "E", "F", "G", "H", "K")] == [
        "2026-10-02", "Train", None, "CHF", 48, 1, None,
    ]
    for column in ("B", "C", "E", "F", "G", "H", "K"):
        assert page_1[f"{column}30"].value is None
        assert workbook["Expenses Page 2"][f"{column}13"].value is None

    validations = []
    for sheet_name in ("Expenses  Page 1", "Expenses Page 2"):
        validations.extend((sheet_name, validation) for validation in workbook[sheet_name].data_validations.dataValidation)
    assert len(validations) == 3
    assert {(sheet, str(item.sqref), item.formula1, item.type, item.allow_blank) for sheet, item in validations} == {
        ("Expenses  Page 1", "G12", "=Codes!$A$23:$A$26", "list", True),
        ("Expenses  Page 1", "K28:K41", "=Codes!$A$3:$A$19", "list", True),
        ("Expenses Page 2", "K13:K47", "=Codes!$A$3:$A$19", "list", True),
    }


def test_xlsx_capacity_and_filename_helpers(tmp_path):
    from django_core_micha.reimbursements import (
        TemplateExportError,
        artifact_filename_base,
        chunk_rows,
        pdf_filename,
        render_xlsx_part,
        sanitize_filename,
        split_beneficiary_name,
        xlsx_filename,
    )

    path = _template_file(tmp_path)
    full = render_xlsx_part(path, _unit(), _rows(49))
    from openpyxl import load_workbook
    workbook = load_workbook(io.BytesIO(full))
    page_1, page_2 = workbook["Expenses  Page 1"], workbook["Expenses Page 2"]
    assert page_1["C28"].value and page_1["C41"].value  # all 14 rows of page 1 are written
    assert page_2["C13"].value and page_2["C47"].value  # and all 35 rows of page 2, the 49th included
    with pytest.raises(TemplateExportError, match=r"Too many reimbursements for one workbook part: 50 > 49\."):
        render_xlsx_part(path, _unit(), _rows(50))

    rows = _rows(5)
    assert list(chunk_rows(rows, 2)) == [rows[:2], rows[2:4], rows[4:]]
    assert split_beneficiary_name("  Ada   Lovelace Byron ") == ("Ada Lovelace", "Byron")
    assert split_beneficiary_name("Single") == ("", "Single")
    assert sanitize_filename("  Zürich & Co.  ") == "zurich-co"
    assert sanitize_filename("東京") == "export"
    assert artifact_filename_base(_unit()) == "zurich-field-work_amy-marie_smith_cc-42"
    assert xlsx_filename(_unit(), 1, 2) == "zurich-field-work_amy-marie_smith_cc-42_part_1.xlsx"
    assert xlsx_filename(_unit(), 1, 1) == "zurich-field-work_amy-marie_smith_cc-42.xlsx"
    assert pdf_filename(_unit()) == "zurich-field-work_amy-marie_smith_cc-42_dossier.pdf"


def test_dossier_page_order_for_card_and_cash(tmp_path):
    from django_core_micha.reimbursements import (
        DossierEntry,
        DossierWithdrawal,
        EvidenceItem,
        render_dossier_pdf,
    )

    substitute = EvidenceItem("cash-1", "substitute_proof", "withdrawal.pdf", "application/pdf", _pdf_with_text("SUBSTITUTE_MARK"), "Substitute")
    receipt = EvidenceItem("card-1", "receipt", "receipt.pdf", "application/pdf", _pdf_with_text("RECEIPT_MARK"), "Receipt")
    bank = EvidenceItem("card-1", "bank_proof", "bank.png", "image/png", _png_evidence(), "Bank")
    withdrawals = [DossierWithdrawal("W-1", "ATM withdrawal summary", ["cash-1"])]
    entries = [
        DossierEntry("card-1", ["CARD-ENTRY", "Payment method: card"], "card", False, [receipt, bank]),
        DossierEntry("cash-1", ["CASH-ENTRY", "Payment method: cash"], "cash", True, [substitute]),
    ]
    result, stats = render_dossier_pdf("Dossier cover", ["Project: TEST"], withdrawals, entries)
    pages = _page_texts(result)
    assert len(pages) == 9
    assert "Dossier cover" in pages[0]
    assert "Cash Withdrawals" in pages[1]
    assert "Withdrawal #W-1 Proof" in pages[2]
    assert "SUBSTITUTE_MARK" in pages[3]
    assert "CARD-ENTRY" in pages[4]
    assert "RECEIPT_MARK" in pages[5]
    assert "CASH-ENTRY" in pages[7]
    assert "Missing Receipt/Substitute" in pages[8]
    assert stats == {
        "included_attachment_count": 3,
        "unsupported_attachment_count": 0,
        "missing_required_count": 1,
        "withdrawal_count": 1,
    }


def test_dossier_missing_proofs_unreadable_and_missing_attachments():
    from django_core_micha.reimbursements import DossierEntry, EvidenceItem, render_dossier_pdf

    no_proofs = DossierEntry("card-2", ["CARD-MISSING"], "card", False, [])
    result, stats = render_dossier_pdf("Cover", [], [], [no_proofs])
    pages = _page_texts(result)
    assert len(pages) == 4
    assert "Missing Receipt/Substitute" in pages[2]
    assert "Missing Bank Proof" in pages[3]
    assert stats["missing_required_count"] == 2

    no_bank_required, no_bank_stats = render_dossier_pdf(
        "Cover", [], [], [no_proofs], bank_proof_required_methods=()
    )
    no_bank_pages = _page_texts(no_bank_required)
    assert len(no_bank_pages) == 3
    assert all("Missing Bank Proof" not in page for page in no_bank_pages)
    assert no_bank_stats["missing_required_count"] == 1

    unreadable = EvidenceItem("cash-3", "receipt", "bad.png", "image/png", b"not an image", "Unreadable")
    missing = EvidenceItem("cash-3", "receipt", "none.png", "image/png", None, "No payload")
    entry = DossierEntry("cash-3", ["CASH-3"], "cash", False, [unreadable, missing])
    rendered, attachment_stats = render_dossier_pdf("Cover", [], [], [entry])
    attachment_pages = _page_texts(rendered)
    assert len(attachment_pages) == 4
    assert "Unsupported Attachment" in attachment_pages[2]
    assert "Attachment Missing" in attachment_pages[3]
    assert attachment_stats == {
        "included_attachment_count": 0,
        "unsupported_attachment_count": 2,
        "missing_required_count": 0,
        "withdrawal_count": 0,
    }


def _statement_pdf():
    from reportlab.lib.colors import red, white
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

    raw = io.BytesIO()
    pdf = canvas.Canvas(raw, pagesize=A4)
    pdf.setTitle("SOURCE_TITLE_LEAK_MARKER")
    pdf.setFillColor(red)
    pdf.rect(52, 738, 320, 35, fill=1, stroke=0)
    pdf.setFillColor(white)
    pdf.drawString(60, 750, "TARGET_TXN_ALPHA_9281")
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString(60, 700, "OTHER_TXN_BETA_7732")
    pdf.setFillColor(white)
    pdf.drawString(60, 725, "HIDDEN_WHITE_MARKER_5521")
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString(60, 620, "OUTSIDE_REGION_GAMMA_1184")
    pdf.showPage()
    pdf.setFillColor(red)
    pdf.rect(52, 738, 320, 35, fill=1, stroke=0)
    pdf.setFillColor(white)
    pdf.drawString(60, 750, "TARGET_TXN_PAGE2_4420")
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString(60, 700, "OTHER_PAGE2_DELTA_5519")
    pdf.showPage()
    pdf.save()

    source_reader = PdfReader(io.BytesIO(raw.getvalue()))
    writer = PdfWriter()
    for page in source_reader.pages:
        writer.add_page(page)
    annotation = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): ArrayObject([FloatObject(5), FloatObject(5), FloatObject(50), FloatObject(50)]),
        NameObject("/Contents"): TextStringObject("ANNOTATION_SECRET_MARKER"),
    })
    writer.add_annotation(page_number=0, annotation=annotation)
    writer.add_attachment("attached.txt", b"ATTACHMENT_SECRET_MARKER")
    writer.add_metadata({"/Title": "SOURCE_TITLE_LEAK_MARKER"})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _image_from_excerpt(pdf_bytes):
    from pypdf import PdfReader

    page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    images = list(page.images)
    assert len(images) == 1
    return images[0].image.convert("RGB")


def test_payment_excerpt_raster_only_and_stacks_regions():
    from PIL import ImageStat
    from pypdf import PdfReader
    from django_core_micha.reimbursements import Region, extract_pdf_text, render_regions_excerpt

    source = _statement_pdf()
    one_region = render_regions_excerpt(
        source,
        [Region(0, (50, 715, 390, 775))],
        context_lines=["Context: payment to Example Store"],
    )
    text = extract_pdf_text(one_region)
    assert "Context: payment to Example Store" in text
    leaked_markers = (
        "TARGET_TXN_ALPHA_9281", "OTHER_TXN_BETA_7732", "HIDDEN_WHITE_MARKER_5521",
        "OUTSIDE_REGION_GAMMA_1184", "TARGET_TXN_PAGE2_4420", "OTHER_PAGE2_DELTA_5519",
        "ANNOTATION_SECRET_MARKER", "ATTACHMENT_SECRET_MARKER", "SOURCE_TITLE_LEAK_MARKER",
    )
    assert all(marker not in text for marker in leaked_markers)
    assert all(marker.encode("ascii") not in one_region for marker in leaked_markers)
    assert b"/Annot" not in one_region
    assert b"/EmbeddedFile" not in one_region
    assert b"/JS" not in one_region
    assert b"SOURCE_TITLE_LEAK_MARKER" not in one_region
    image = _image_from_excerpt(one_region)
    assert sum(ImageStat.Stat(image).var) > 100
    # The embedded image is exactly the requested region (340 x 60 points at the default scale 3.0), not the
    # whole page: rasterizing more than the region would change its pixel size.
    assert abs(image.width - 340 * 3) <= 2 and abs(image.height - 60 * 3) <= 2  # pdfium rounds by at most a pixel
    excerpt_page = PdfReader(io.BytesIO(one_region)).pages[0]
    assert float(excerpt_page.mediabox.height) < 400  # a tight page, not a statement-sized A4 sheet

    two_regions = render_regions_excerpt(
        source,
        [Region(0, (50, 715, 390, 775)), Region(1, (50, 715, 390, 775))],
    )
    stacked_image = _image_from_excerpt(two_regions)
    assert stacked_image.height > image.height
    assert len(PdfReader(io.BytesIO(two_regions)).pages) == 1


def test_payment_excerpt_fails_closed_for_invalid_oversized_and_encrypted_inputs():
    from pypdf import PdfReader, PdfWriter
    from django_core_micha.reimbursements import ExcerptError, Region, render_regions_excerpt

    source = _statement_pdf()
    region = Region(0, (50, 715, 390, 775))
    invalid_calls = [
        (source, [], {}, "invalid_region"),
        (source, [region] * 13, {}, "invalid_region"),
        (source, [Region(0, (10, 10, 700, 900))], {}, "invalid_region"),
        (source, [Region(2, (50, 715, 390, 775))], {}, "invalid_region"),
        (source, [region], {"max_pixels": 100}, "too_large"),
        (b"not a PDF", [region], {}, "invalid_pdf"),
    ]
    for pdf_bytes, regions, options, code in invalid_calls:
        with pytest.raises(ExcerptError) as raised:
            render_regions_excerpt(pdf_bytes, regions, **options)
        assert raised.value.code == code

    source_reader = PdfReader(io.BytesIO(source))
    encrypted_writer = PdfWriter()
    for page in source_reader.pages:
        encrypted_writer.add_page(page)
    encrypted_writer.encrypt("sensitive")
    encrypted = io.BytesIO()
    encrypted_writer.write(encrypted)
    with pytest.raises(ExcerptError) as raised:
        render_regions_excerpt(encrypted.getvalue(), [region])
    assert raised.value.code == "encrypted"


def test_payment_excerpt_refuses_a_rotated_statement_page():
    import io
    import pytest
    from pypdf import PdfReader, PdfWriter
    from django_core_micha.reimbursements import ExcerptError, Region, render_regions_excerpt

    reader = PdfReader(io.BytesIO(_statement_pdf()))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.pages[0].rotate(90)
    rotated = io.BytesIO()
    writer.write(rotated)
    with pytest.raises(ExcerptError) as raised:
        render_regions_excerpt(rotated.getvalue(), [Region(0, (50, 715, 390, 775))])
    assert raised.value.code == "invalid_region"  # no excerpt from a turned page, no fallback to the source


def test_xlsx_user_text_starting_with_equals_stays_literal(tmp_path):
    import io
    from openpyxl import load_workbook
    from django_core_micha.reimbursements import render_xlsx_part

    path = _template_file(tmp_path)
    hostile = '=HYPERLINK("https://attacker.example","Open receipt")'
    rows = _rows(1)
    rows[0] = rows[0].__class__(**{**rows[0].__dict__, "merchant_name": hostile})
    unit = _unit()
    unit = unit.__class__(**{**unit.__dict__, "beneficiary_address": "=1+1"})
    workbook = load_workbook(io.BytesIO(render_xlsx_part(path, unit, rows)))
    page_1 = workbook["Expenses  Page 1"]
    assert page_1["C28"].data_type == "s" and page_1["C28"].value.startswith(hostile)
    assert page_1["C9"].data_type == "s" and page_1["C9"].value == "=1+1"


def test_xlsx_account_categories_map_to_both_expense_sheets(tmp_path):
    from dataclasses import replace
    from openpyxl import load_workbook
    from django_core_micha.reimbursements import ExportRow, render_xlsx_part

    path = _template_file(tmp_path)
    rows = _rows(15)
    rows[0] = replace(rows[0], account="422000 Travel costs")
    rows[14] = replace(rows[14], account="Other expenses")
    workbook = load_workbook(io.BytesIO(render_xlsx_part(path, _unit(), rows)))
    assert workbook["Expenses  Page 1"]["K28"].value == "422000 Travel costs"
    assert workbook["Expenses Page 2"]["K13"].value == "Other expenses"
    assert workbook["Expenses  Page 1"]["K29"].value is None


@pytest.mark.parametrize("account", ["unknown category", '=HYPERLINK("https://attacker.example","open")', [], {}])
def test_xlsx_rejects_invalid_or_formula_like_account_categories(tmp_path, account):
    from dataclasses import replace
    from django_core_micha.reimbursements import ExportRow, TemplateExportError, render_xlsx_part

    row = _rows(1)[0]
    row = replace(row, account=account)
    with pytest.raises(TemplateExportError, match="Invalid reimbursement account category"):
        render_xlsx_part(_template_file(tmp_path), _unit(), [row])
