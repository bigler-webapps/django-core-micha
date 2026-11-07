"""Secure rasterized payment excerpts from private statement PDFs."""

from __future__ import annotations

import io
import math
import textwrap
from dataclasses import dataclass
from typing import Sequence


class ExcerptError(Exception):
    """A fail-closed excerpt error with a stable machine-readable code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Region:
    """A PDF page crop in user-space points, with origin at bottom-left."""

    page_index: int
    bbox: tuple[float, float, float, float]


def _region_pixels(region: Region, box: tuple[float, float, float, float], scale: float):
    x0, y0, x1, y1 = region.bbox
    bx0, by0, bx1, by1 = box
    tolerance = 0.5
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        raise ExcerptError("invalid_region")
    if x0 >= x1 or y0 >= y1:
        raise ExcerptError("invalid_region")
    if x0 < bx0 - tolerance or y0 < by0 - tolerance or x1 > bx1 + tolerance or y1 > by1 + tolerance:
        raise ExcerptError("invalid_region")

    # Tiny rounding excursions are clamped to the visible page box.
    x0 = max(x0, bx0)
    y0 = max(y0, by0)
    x1 = min(x1, bx1)
    y1 = min(y1, by1)
    width = max(1, math.ceil((x1 - x0) * scale))
    height = max(1, math.ceil((y1 - y0) * scale))
    return (x0, y0, x1, y1), (width, height)


MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_PAGES = 400


def render_regions_excerpt(
    pdf_bytes: bytes,
    regions: Sequence[Region],
    *,
    context_lines: Sequence[str] = (),
    title: str = "Payment excerpt",
    scale: float = 3.0,
    max_pixels: int = 16_000_000,
) -> bytes:
    """Rasterize selected PDF regions into a new PDF containing no source objects.

    Region bounding boxes use PDF user-space points, with ``(0, 0)`` at the
    bottom-left of the page box. The resulting excerpt carries only new text
    from ``context_lines`` and a raster image of the selected regions.
    """
    from pypdf import PdfReader

    try:
        source_reader = PdfReader(io.BytesIO(pdf_bytes))
        if source_reader.is_encrypted:
            raise ExcerptError("encrypted")
        if len(pdf_bytes) > MAX_SOURCE_BYTES or len(source_reader.pages) > MAX_SOURCE_PAGES:
            raise ExcerptError("too_large")  # bounded work before any per-page traversal
        page_boxes = []
        for page in source_reader.pages:
            user_unit = page.get("/UserUnit")
            if user_unit is not None and float(user_unit) != 1.0:
                raise ExcerptError("invalid_pdf")  # a scaled page would break the pixel budget and the region mapping
            box = page.cropbox
            page_box = (float(box.left), float(box.bottom), float(box.right), float(box.top))
            if (
                not all(math.isfinite(value) for value in page_box)
                or page_box[0] >= page_box[2]
                or page_box[1] >= page_box[3]
            ):
                raise ExcerptError("invalid_pdf")
            page_boxes.append(page_box)
    except ExcerptError:
        raise
    except Exception as exc:
        raise ExcerptError("invalid_pdf") from exc

    try:
        region_list = list(regions)
    except Exception as exc:
        raise ExcerptError("invalid_region") from exc
    if not region_list or len(region_list) > 12:
        raise ExcerptError("invalid_region")
    try:
        scale = float(scale)
        max_pixels = int(max_pixels)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExcerptError("invalid_region") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ExcerptError("invalid_region")
    if max_pixels <= 0:
        raise ExcerptError("too_large")

    validated: list[tuple[int, tuple[float, float, float, float], tuple[int, int]]] = []
    for region in region_list:
        if not isinstance(region, Region) or not isinstance(region.page_index, int):
            raise ExcerptError("invalid_region")
        if region.page_index < 0 or region.page_index >= len(page_boxes):
            raise ExcerptError("invalid_region")
        try:
            bbox, dimensions = _region_pixels(region, page_boxes[region.page_index], scale)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExcerptError("invalid_region") from exc
        validated.append((region.page_index, bbox, dimensions))

    crop_dimensions = [dimensions for _, _, dimensions in validated]
    stacked_width = max(width for width, _ in crop_dimensions)
    separator_height = max(1, round(scale * 3))
    stacked_height = sum(height for _, height in crop_dimensions) + separator_height * (len(crop_dimensions) - 1)
    if stacked_width * stacked_height > max_pixels:
        raise ExcerptError("too_large")
    render_page_pixels = 0
    for page_index in {page_index for page_index, _, _ in validated}:
        bx0, by0, bx1, by1 = page_boxes[page_index]
        render_page_pixels += math.ceil((bx1 - bx0) * scale) * math.ceil((by1 - by0) * scale)
    if render_page_pixels > max_pixels:
        raise ExcerptError("too_large")

    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        raise ExcerptError("render_failed") from exc

    document = None
    try:
        try:
            document = pdfium.PdfDocument(pdf_bytes)
        except Exception as exc:
            raise ExcerptError("invalid_pdf") from exc
        if len(document) != len(page_boxes):
            raise ExcerptError("invalid_pdf")

        from PIL import Image

        page_cache = {}
        crops = []
        for page_index, bbox, _ in validated:
            if page_index not in page_cache:
                page = document.get_page(page_index)
                try:
                    # The region is given in the unrotated page box; a page with /Rotate would be rasterized
                    # turned and the crop would hit another part of the page. Fail closed instead of guessing.
                    if page.get_rotation() % 360:
                        raise ExcerptError("invalid_region")
                    bitmap = page.render(scale=scale)
                    try:
                        page_cache[page_index] = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()

            image = page_cache[page_index]
            bx0, by0, bx1, by1 = page_boxes[page_index]
            x0, y0, x1, y1 = bbox
            # Map PDF bottom-left coordinates to the top-left raster grid.
            px_per_x = image.width / (bx1 - bx0)
            px_per_y = image.height / (by1 - by0)
            left = math.floor((x0 - bx0) * px_per_x)
            right = math.ceil((x1 - bx0) * px_per_x)
            top = math.floor((by1 - y1) * px_per_y)
            bottom = math.ceil((by1 - y0) * px_per_y)
            left = max(0, min(image.width - 1, left))
            top = max(0, min(image.height - 1, top))
            right = max(left + 1, min(image.width, right))
            bottom = max(top + 1, min(image.height, bottom))
            crops.append(image.crop((left, top, right, bottom)))

        stacked_width = max(image.width for image in crops)
        separator_height = max(1, round(scale * 3))
        stacked_height = sum(image.height for image in crops) + separator_height * (len(crops) - 1)
        if stacked_width * stacked_height > max_pixels:
            raise ExcerptError("too_large")
        stacked = Image.new("RGB", (stacked_width, stacked_height), "white")
        offset_y = 0
        for index, crop in enumerate(crops):
            stacked.paste(crop, ((stacked_width - crop.width) // 2, offset_y))
            offset_y += crop.height + (separator_height if index < len(crops) - 1 else 0)

        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
        from pypdf import PdfWriter

        page_width, page_height = A4
        margin = 36
        text_lines = []
        for line in context_lines:
            text_lines.extend(textwrap.wrap(str(line), width=105) or [""])
        text_height = len(text_lines) * 12
        gap = 8 if text_lines else 0
        available_height = page_height - margin * 2 - text_height - gap
        available_width = page_width - margin * 2
        if available_height <= 0 or available_width <= 0:
            raise ExcerptError("render_failed")
        fit_scale = min(available_width / stacked.width, available_height / stacked.height)
        image_width = stacked.width * fit_scale
        image_height = stacked.height * fit_scale
        # The page is as tall as its content (A4 width, never taller than A4): a short excerpt must not
        # float at the bottom of an otherwise empty page.
        content_height = margin * 2 + text_height + gap + image_height

        rendered = io.BytesIO()
        pdf = canvas.Canvas(rendered, pagesize=(page_width, content_height))
        pdf.setTitle(str(title or "Payment excerpt"))
        pdf.setAuthor("")
        pdf.setCreator("django-core-micha reimbursements")
        y = content_height - margin
        pdf.setFont("Helvetica", 9)
        for line in text_lines:
            y -= 12
            # Canvas escapes PDF string delimiters and control characters.
            pdf.drawString(margin, y, line)
        image_x = (page_width - image_width) / 2
        pdf.drawImage(
            ImageReader(stacked), image_x, margin, width=image_width,
            height=image_height, preserveAspectRatio=True, anchor="c",
        )
        pdf.showPage()
        pdf.save()

        clean_reader = PdfReader(io.BytesIO(rendered.getvalue()))
        clean_writer = PdfWriter()
        for page in clean_reader.pages:
            clean_writer.add_page(page)
        clean_writer.add_metadata({
            "/Title": str(title or "Payment excerpt"),
            "/Author": "",
            "/Creator": "django-core-micha reimbursements",
        })
        clean_output = io.BytesIO()
        clean_writer.write(clean_output)
        return clean_output.getvalue()
    except ExcerptError:
        raise
    except Exception as exc:
        raise ExcerptError("render_failed") from exc
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from each page of a PDF, primarily for verification."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
