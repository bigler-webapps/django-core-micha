"""Provider-agnostic one-shot document extraction."""

from .errors import DocumentExtractionError
from .input_prep import encode_image_base64, extract_pdf_text, resize_image_bytes
from .salvage import extract_json_payload, require_json_payload
from .schema_contract import validate_schema
from .service import extract_document
from .types import ExtractionRequest, ExtractionResult, TokenUsage

__all__ = [
    "DocumentExtractionError",
    "ExtractionRequest",
    "ExtractionResult",
    "TokenUsage",
    "encode_image_base64",
    "extract_document",
    "extract_json_payload",
    "extract_pdf_text",
    "require_json_payload",
    "resize_image_bytes",
    "validate_schema",
]
