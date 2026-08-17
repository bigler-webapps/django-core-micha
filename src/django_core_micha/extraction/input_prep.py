"""Provider-free preparation of document inputs."""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from .errors import (
    DocumentExtractionError,
    EMPTY_FILE,
    IMAGE_CONVERSION_FAILED,
    MISSING_DEPENDENCY,
)


def resize_image_bytes(
    content: bytes,
    mime_type: str,
    *,
    max_long_edge: int = 1500,
    jpeg_quality: int = 85,
) -> tuple[bytes, str]:
    """Downscale an image if needed and return optimized RGB JPEG bytes."""
    if not content:
        raise DocumentExtractionError(EMPTY_FILE, "The uploaded file is empty.")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.mode != "RGB":
                image = image.convert("RGB")
            if max(image.width, image.height) > max_long_edge:
                image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=jpeg_quality,
                optimize=True,
            )
    except (OSError, ValueError, SyntaxError) as exc:
        raise DocumentExtractionError(
            IMAGE_CONVERSION_FAILED,
            "The uploaded image could not be converted.",
        ) from exc
    return output.getvalue(), "image/jpeg"


def encode_image_base64(content: bytes, mime_type: str) -> tuple[str, str]:
    """Return provider-neutral base64 data and its MIME type."""
    if not content:
        raise DocumentExtractionError(EMPTY_FILE, "The uploaded file is empty.")
    return base64.b64encode(content).decode("ascii"), mime_type


def extract_pdf_text(content: bytes, *, character_cap: int = 12000) -> str:
    """Extract PDF text up to the cap; tolerate malformed non-empty PDFs."""
    if not content:
        raise DocumentExtractionError(EMPTY_FILE, "The uploaded file is empty.")
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise DocumentExtractionError(
            MISSING_DEPENDENCY,
            "PDF extraction support is not installed.",
            500,
        ) from exc

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return ""

    chunks: list[str] = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text:
            chunks.append(page_text)
    return "\n".join(chunks).strip()[:character_cap]
