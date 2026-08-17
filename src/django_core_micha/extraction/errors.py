"""Provider-neutral errors for one-shot document extraction."""

from __future__ import annotations


ERROR_PREFIX = "django_core_micha.extraction"

MISSING_CREDENTIAL = f"{ERROR_PREFIX}.missing_credential"
MISSING_MODEL = f"{ERROR_PREFIX}.missing_model"
MISSING_DEPENDENCY = f"{ERROR_PREFIX}.missing_dependency"
EMPTY_FILE = f"{ERROR_PREFIX}.empty_file"
IMAGE_CONVERSION_FAILED = f"{ERROR_PREFIX}.image_conversion_failed"
REQUEST_FAILED = f"{ERROR_PREFIX}.request_failed"
OUTPUT_TRUNCATED = f"{ERROR_PREFIX}.output_truncated"
INVALID_JSON = f"{ERROR_PREFIX}.invalid_json"
EMPTY_PAYLOAD = f"{ERROR_PREFIX}.empty_payload"


class DocumentExtractionError(Exception):
    """A stable, provider-neutral extraction failure."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        *,
        response=None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.response = response
