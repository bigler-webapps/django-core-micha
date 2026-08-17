"""One-call dispatch with mandatory post-response cost metering."""

from __future__ import annotations

from typing import Any

from .cost_guard import charge_user_for_response
from .drivers import anthropic_driver, openai_driver
from .errors import DocumentExtractionError, REQUEST_FAILED
from .types import ExtractionRequest, ExtractionResult


def extract_document(
    request: ExtractionRequest,
    *,
    provider: str,
    api_key: str,
    model: str,
    user_id: int | None,
    client: Any = None,
    **provider_options: Any,
) -> ExtractionResult:
    """Run one provider request, then charge its reported usage."""
    drivers = {
        "openai": openai_driver.extract,
        "anthropic": anthropic_driver.extract,
    }
    try:
        driver = drivers[provider.lower()]
    except (AttributeError, KeyError) as exc:
        raise DocumentExtractionError(
            REQUEST_FAILED,
            "The configured AI provider is not supported.",
            500,
        ) from exc
    try:
        result = driver(
            request,
            api_key=api_key,
            model=model,
            client=client,
            **provider_options,
        )
    except DocumentExtractionError as exc:
        if exc.response is not None:
            charge_user_for_response(
                user_id=user_id,
                response=exc.response,
                model=model,
            )
        raise
    charge_user_for_response(user_id=user_id, response=result, model=model)
    return result
