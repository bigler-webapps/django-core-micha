"""Anthropic driver for normalized one-shot document extraction."""

from __future__ import annotations

from typing import Any

from ..errors import (
    DocumentExtractionError,
    MISSING_CREDENTIAL,
    MISSING_DEPENDENCY,
    MISSING_MODEL,
    OUTPUT_TRUNCATED,
    REQUEST_FAILED,
)
from ..input_prep import encode_image_base64
from ..salvage import response_to_data
from ..schema_contract import validate_schema
from ..types import ExtractionRequest, ExtractionResult
from ._common import best_effort_delete, merge_passthrough, usage_from_response

_FILES_BETA = "files-api-2025-04-14"


def extract(
    request: ExtractionRequest,
    *,
    api_key: str,
    model: str,
    client: Any = None,
    **extra: Any,
) -> ExtractionResult:
    """Issue one Anthropic Messages API extraction request."""
    validate_schema(request.schema)
    if not api_key:
        raise DocumentExtractionError(MISSING_CREDENTIAL, "AI provider credential is missing.", 500)
    if not model:
        raise DocumentExtractionError(MISSING_MODEL, "AI provider model is missing.", 500)
    if client is None:
        try:
            from anthropic import Anthropic
        except Exception as exc:
            raise DocumentExtractionError(
                MISSING_DEPENDENCY,
                "The configured AI provider SDK is not installed.",
                500,
            ) from exc
        client = Anthropic(api_key=api_key)

    upload = None
    try:
        content: list[dict[str, Any]] = []
        using_upload = request.upload_bytes is not None
        if request.image_bytes is not None:
            encoded, mime_type = encode_image_base64(
                request.image_bytes,
                request.image_mime_type or "application/octet-stream",
            )
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": encoded,
                    },
                }
            )
        elif using_upload:
            try:
                upload = client.beta.files.upload(
                    file=(request.upload_filename, request.upload_bytes, request.upload_mime_type)
                )
            except Exception as exc:
                raise DocumentExtractionError(
                    REQUEST_FAILED,
                    "The AI provider file upload failed.",
                    getattr(exc, "status_code", 502) or 502,
                ) from exc
            block_type = "document" if (request.upload_mime_type or "").lower() == "application/pdf" else "image"
            content.append({"type": block_type, "source": {"type": "file", "file_id": upload.id}})

        text = request.user_prompt
        if request.pdf_text:
            text = f"{text}\n\nExtracted PDF text:\n{request.pdf_text}"
        content.append({"type": "text", "text": text})

        try:
            call = merge_passthrough(
                {
                    "model": model,
                    "system": request.system_prompt,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": request.max_output_tokens,
                    "thinking": dict(request.thinking),
                    "output_config": {
                        "effort": request.effort,
                        "format": {
                            "type": "json_schema",
                            "schema": request.schema,
                        },
                    },
                },
                extra,
            )
        except ValueError as exc:
            raise DocumentExtractionError(REQUEST_FAILED, str(exc), 400) from exc
        try:
            if using_upload:
                response = client.beta.messages.create(betas=[_FILES_BETA], **call)
            else:
                response = client.messages.create(**call)
        except Exception as exc:
            raise DocumentExtractionError(
                REQUEST_FAILED,
                "The AI provider request failed.",
                getattr(exc, "status_code", 502) or 502,
            ) from exc

        if getattr(response, "stop_reason", None) == "max_tokens":
            raise DocumentExtractionError(
                OUTPUT_TRUNCATED,
                "The AI provider output was truncated.",
                502,
                response=response,
            )
        raw_text = "\n".join(
            str(getattr(block, "text", ""))
            for block in (getattr(response, "content", []) or [])
            if getattr(block, "type", "") == "text" and getattr(block, "text", "")
        ).strip()
        return ExtractionResult(raw_text, response_to_data(response), usage_from_response(response))
    finally:
        if upload is not None:
            best_effort_delete(lambda: client.beta.files.delete(upload.id))
