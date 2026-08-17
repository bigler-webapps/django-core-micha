"""OpenAI driver for normalized one-shot document extraction."""

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


def extract(
    request: ExtractionRequest,
    *,
    api_key: str,
    model: str,
    client: Any = None,
    **extra: Any,
) -> ExtractionResult:
    """Issue one OpenAI Responses API extraction request."""
    validate_schema(request.schema)
    if not api_key:
        raise DocumentExtractionError(MISSING_CREDENTIAL, "AI provider credential is missing.", 500)
    if not model:
        raise DocumentExtractionError(MISSING_MODEL, "AI provider model is missing.", 500)
    if client is None:
        try:
            from openai import OpenAI
        except Exception as exc:
            raise DocumentExtractionError(
                MISSING_DEPENDENCY,
                "The configured AI provider SDK is not installed.",
                500,
            ) from exc
        client = OpenAI(api_key=api_key)

    upload = None
    try:
        content: list[dict[str, Any]] = []
        text = request.user_prompt
        if request.pdf_text:
            text = f"{text}\n\nExtracted PDF text:\n{request.pdf_text}"
        content.append({"type": "input_text", "text": text})
        if request.image_bytes is not None:
            encoded, mime_type = encode_image_base64(
                request.image_bytes,
                request.image_mime_type or "application/octet-stream",
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                }
            )
        elif request.upload_bytes is not None:
            try:
                upload, _purpose = _upload_file(
                    client,
                    request.upload_filename,
                    request.upload_mime_type,
                    request.upload_bytes,
                )
            except Exception as exc:
                raise DocumentExtractionError(
                    REQUEST_FAILED,
                    "The AI provider file upload failed.",
                    getattr(exc, "status_code", 502) or 502,
                ) from exc
            content.append({"type": "input_file", "file_id": upload.id})

        try:
            call = merge_passthrough(
                {
                    "model": model,
                    "instructions": request.system_prompt,
                    "input": [{"role": "user", "content": content}],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "document_extraction",
                            "strict": True,
                            "schema": request.schema,
                        }
                    },
                    "max_output_tokens": request.max_output_tokens,
                    "reasoning": {"effort": request.effort},
                },
                extra,
            )
        except ValueError as exc:
            raise DocumentExtractionError(REQUEST_FAILED, str(exc), 400) from exc
        try:
            response = client.responses.create(**call)
        except Exception as exc:
            raise DocumentExtractionError(
                REQUEST_FAILED,
                "The AI provider request failed.",
                getattr(exc, "status_code", 502) or 502,
            ) from exc

        if getattr(response, "status", None) == "incomplete":
            raise DocumentExtractionError(
                OUTPUT_TRUNCATED,
                "The AI provider output was truncated.",
                502,
                response=response,
            )
        raw_text = _response_text(response)
        return ExtractionResult(raw_text, response_to_data(response), usage_from_response(response))
    finally:
        if upload is not None:
            best_effort_delete(lambda: client.files.delete(upload.id))


def _upload_file(client: Any, filename: str, mime_type: str, content: bytes) -> tuple[Any, str]:
    """Upload with a purpose fallback, mirroring the app's pre-migration behaviour."""
    file_tuple = (filename, content, mime_type)
    last_exception: Exception | None = None
    for purpose in ("user_data", "assistants"):
        try:
            return client.files.create(file=file_tuple, purpose=purpose), purpose
        except Exception as exc:
            last_exception = exc
            continue
    raise last_exception


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return str(output_text)
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", "") in {"output_text", "text"}:
                value = getattr(content, "text", "")
                if hasattr(value, "value"):
                    value = value.value
                if value:
                    parts.append(str(value))
    return "\n".join(parts).strip()
