"""Normalized request and result types for document extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExtractionRequest:
    system_prompt: str
    user_prompt: str
    schema: dict[str, Any]
    max_output_tokens: int
    effort: str
    thinking: Mapping[str, Any]
    image_bytes: bytes | None = None
    image_mime_type: str | None = None
    pdf_text: str | None = None
    upload_bytes: bytes | None = None
    upload_mime_type: str | None = None
    upload_filename: str | None = None

    def __post_init__(self) -> None:
        if self.thinking is None:
            raise ValueError("thinking must be supplied explicitly")
        if self.image_bytes is not None and not self.image_mime_type:
            raise ValueError("image_mime_type is required when image_bytes are supplied")
        if self.upload_bytes is not None and not (self.upload_mime_type and self.upload_filename):
            raise ValueError(
                "upload_mime_type and upload_filename are required when upload_bytes are supplied"
            )
        if self.image_bytes is not None and self.upload_bytes is not None:
            raise ValueError("image_bytes and upload_bytes are mutually exclusive")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ExtractionResult:
    raw_text: str
    raw_response: Any
    usage: TokenUsage
