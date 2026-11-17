"""One-call dispatch with pre-provider budget admission and usage settlement."""

from __future__ import annotations

import uuid
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


def _record_usage(*, response, provider, model, user_id, purpose, reference, source_id):
    from django.apps import apps
    if not apps.is_installed("django_core_micha.billing"):
        return
    from django.contrib.auth import get_user_model
    from django_core_micha.billing.services import record_ai_call
    from .drivers._common import usage_from_response
    usage = usage_from_response(response)
    user = get_user_model().objects.filter(pk=user_id).first() if user_id is not None else None
    record_ai_call(user=user, provider=provider, model=model, input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens, cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens, purpose=purpose, reference=reference,
        source_id=source_id, usage_reported=usage.reported)


def extract_document(
    request: ExtractionRequest,
    *,
    provider: str,
    api_key: str,
    model: str,
    user_id: int | None,
    source_id: str = "",
    reference: str = "",
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

    from django.apps import apps
    if apps.is_installed("django_core_micha.billing"):
        from django_core_micha.billing.services import require_ai_usage_adapter
        try:
            require_ai_usage_adapter(user_id, source_id, "extraction")
        except ValueError as exc:
            raise DocumentExtractionError(REQUEST_FAILED, "AI accounting is unavailable.", 503) from exc

    reservation = reserve_for_extraction(user_id=user_id, request=request, model=model)
    reference = str(reference or f"extraction:{uuid.uuid4()}")
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
            try:
                _record_usage(response=exc.response, provider=provider, model=model, user_id=user_id,
                              purpose="extraction", reference=reference, source_id=source_id)
            except Exception:
                import logging
                logging.getLogger(__name__).error("Extraction billing cost hook failed (%s)", __import__("sys").exc_info()[0].__name__)
        elif _definitively_unbilled(exc):
            release_reservation(reservation=reservation)
        elif exc.provider_request_started:
            try:
                _record_usage(response=None, provider=provider, model=model, user_id=user_id,
                              purpose="extraction", reference=reference, source_id=source_id)
            except Exception:
                import logging
                logging.getLogger(__name__).error("Extraction billing cost hook failed (%s)", __import__("sys").exc_info()[0].__name__)
        # Ambiguous provider failures keep their reservation until bounded expiry.
        raise
    except Exception:
        # A transport or adapter failure without a usage object is unresolved
        # once the provider adapter has been entered; do not synthesize tokens.
        try:
            _record_usage(response=None, provider=provider, model=model, user_id=user_id,
                          purpose="extraction", reference=reference, source_id=source_id)
        except Exception:
            import logging
            logging.getLogger(__name__).error("Extraction billing cost hook failed (%s)", __import__("sys").exc_info()[0].__name__)
        raise
    # Any other exception propagates with the reservation held: it may have
    # happened after the provider billed the call.

    if reservation is None:
        # Disabled/local and anonymous calls preserve their old post-response
        # accounting semantics (which are no-ops when accounting is disabled).
        charge_user_for_response(user_id=user_id, response=result, model=model)
    else:
        settle_reservation(reservation=reservation, response=result, model=model)
    try:
        _record_usage(response=result, provider=provider, model=model, user_id=user_id,
                      purpose="extraction", reference=reference, source_id=source_id)
    except Exception:
        import logging
        logging.getLogger(__name__).error("Extraction billing cost hook failed (%s)", __import__("sys").exc_info()[0].__name__)
    return result
