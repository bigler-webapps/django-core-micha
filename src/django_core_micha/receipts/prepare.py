"""Sniff and prepare allowlisted receipt files without trusting their names."""
from dataclasses import dataclass
from .errors import ReceiptError
from django_core_micha.extraction import DocumentExtractionError, extract_pdf_text, resize_image_bytes
from django_core_micha.extraction.errors import IMAGE_CONVERSION_FAILED

MAX_FILE_BYTES=10*1024*1024
@dataclass(frozen=True)
class PreparedInput:
    image_bytes:bytes|None=None; image_mime_type:str|None=None; pdf_text:str|None=None; upload_bytes:bytes|None=None; upload_mime_type:str|None=None; upload_filename:str|None=None

def sniff_type(content):
    if content.startswith(b"\xff\xd8\xff"):return "jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):return "png"
    if content.startswith(b"%PDF"):return "pdf"
    if len(content)>=12 and content[4:8]==b"ftyp" and content[8:12] in {b"heic",b"heix",b"mif1",b"heif"}:return "heic"
    raise ReceiptError("unsupported_file_type")

MAX_PIXELS=40_000_000  # bounds decoding work: the file limit alone does not (a small hostile file can declare a huge canvas)

def _check_pixels(content):
    from io import BytesIO
    try:
        from PIL import Image
        with Image.open(BytesIO(content)) as im:
            if im.width*im.height>MAX_PIXELS:raise ReceiptError("unsupported_file_type")
    except ReceiptError:raise
    except Exception as exc:raise ReceiptError("unsupported_file_type") from exc

def prepare(content):
    if not content:raise ReceiptError("invalid_request")
    if len(content)>MAX_FILE_BYTES:raise ReceiptError("file_too_large")
    kind=sniff_type(content)
    if kind=="pdf":
        text=extract_pdf_text(content)
        return PreparedInput(pdf_text=text) if text else PreparedInput(upload_bytes=content,upload_mime_type="application/pdf",upload_filename="receipt.pdf")
    if kind=="heic":
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception as exc:raise RuntimeError("HEIC support unavailable") from exc
        mime="image/heic"
    else:mime="image/jpeg" if kind=="jpeg" else "image/png"
    _check_pixels(content)
    try:image,mime=resize_image_bytes(content,mime)
    except DocumentExtractionError as exc:
        if exc.code==IMAGE_CONVERSION_FAILED:raise ReceiptError("unsupported_file_type") from exc  # an unreadable image is a bad file, not a provider fault
        raise
    return PreparedInput(image_bytes=image,image_mime_type=mime)

