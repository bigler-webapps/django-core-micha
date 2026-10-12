"""One-call dispatch with pre-provider budget admission and usage settlement."""

from __future__ import annotations

from typing import Any

from .cost_guard import (
    charge_user_for_response,
    release_reservation,
    reserve_for_extraction,
    settle_reservation,
)
from .drivers import anthropic_driver, openai_driver
from .errors import DocumentExtractionError, REQUEST_FAILED
from .types import ExtractionRequest, ExtractionResult


def _definitively_unbilled(exc: DocumentExtractionError) -> bool:
    if not exc.provider_request_started:
        return True
    status = int(exc.status_code or 0)
    return 400 <= status < 500 and status not in {408, 409, 429}


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
    """Reserve budget, run one provider request, then settle reported usage."""
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

    reservation = reserve_for_extraction(user_id=user_id, request=request, model=model)
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
            settle_reservation(reservation=reservation, response=exc.response, model=model)
        elif _definitively_unbilled(exc):
            release_reservation(reservation=reservation)
        # Ambiguous provider failures keep their reservation until bounded expiry.
        raise
    # Any other exception propagates with the reservation held: it may have
    # happened after the provider billed the call.

    if reservation is None:
        # Disabled/local and anonymous calls preserve their old post-response
        # accounting semantics (which are no-ops when accounting is disabled).
        charge_user_for_response(user_id=user_id, response=result, model=model)
    else:
        settle_reservation(reservation=reservation, response=result, model=model)
    return result
