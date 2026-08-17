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

    def __post_init__(self) -> None:
        if self.thinking is None:
            raise ValueError("thinking must be supplied explicitly")
        if self.image_bytes is not None and not self.image_mime_type:
            raise ValueError("image_mime_type is required when image_bytes are supplied")


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
