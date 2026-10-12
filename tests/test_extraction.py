"""Focused no-network tests for provider-agnostic document extraction."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.test import override_settings
from PIL import Image

from django_core_micha.extraction.cost_guard import (
    AICostAccountingUnavailable,
    AICostLimitExceeded,
    _cache_key,
    _cache_key_for_day,
    _read_counter,
    _reservation_buckets,
    _reservation_key,
    _today_key,
    charge_user,
    charge_user_for_response,
    current_user_spend_cents,
    estimate_cost_cents,
    release_reservation,
    reserve_for_extraction,
    settle_reservation,
)
from django_core_micha.extraction.drivers import anthropic_driver, openai_driver
from django_core_micha.extraction.errors import (
    DocumentExtractionError,
    IMAGE_CONVERSION_FAILED,
    INVALID_JSON,
    OUTPUT_TRUNCATED,
    REQUEST_FAILED,
)
from django_core_micha.extraction.input_prep import extract_pdf_text, resize_image_bytes
from django_core_micha.extraction.salvage import extract_json_payload
from django_core_micha.extraction.schema_contract import validate_schema
from django_core_micha.extraction.service import extract_document
from django_core_micha.extraction.types import ExtractionRequest, ExtractionResult


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _image_bytes(*, size=(40, 20), mode="RGB") -> bytes:
    image = Image.new(mode, size)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _valid_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "details": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"count": {"type": "number"}},
            },
        },
    }


def _request(*, image=False, upload_mime=None) -> ExtractionRequest:
    return ExtractionRequest(
        system_prompt="Extract only the requested fields.",
        user_prompt="Read this document.",
        schema=_valid_schema(),
        max_output_tokens=321,
        effort="low",
        thinking={"type": "disabled"},
        image_bytes=_image_bytes() if image else None,
        image_mime_type="image/png" if image else None,
        pdf_text=None if upload_mime else "Visible PDF text",
        upload_bytes=b"raw-attachment-bytes" if upload_mime else None,
        upload_mime_type=upload_mime,
        upload_filename="scan.pdf" if upload_mime else None,
    )


class _RecordingEndpoint:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class _ProviderFailure(Exception):
    status_code = 429


class _RecordingFiles:
    def __init__(self, *, file_id="file-123", upload_error=None, delete_error=None, purpose_sequence=None):
        self.file_id = file_id
        self.upload_error = upload_error
        self.delete_error = delete_error
        self.purpose_sequence = purpose_sequence  # OpenAI-only: purposes that raise before one succeeds
        self.create_calls = []
        self.upload_calls = []
        self.delete_calls = []

    def create(self, *, file, purpose):
        """OpenAI-shaped: client.files.create(file=..., purpose=...)."""
        self.create_calls.append(purpose)
        if self.purpose_sequence and purpose in self.purpose_sequence:
            raise RuntimeError(f"purpose {purpose} rejected")
        if self.upload_error:
            raise self.upload_error
        return SimpleNamespace(id=self.file_id)

    def upload(self, *, file):
        """Anthropic-shaped: client.beta.files.upload(file=...)."""
        self.upload_calls.append(file)
        if self.upload_error:
            raise self.upload_error
        return SimpleNamespace(id=self.file_id)

    def delete(self, file_id):
        self.delete_calls.append(file_id)
        if self.delete_error:
            raise self.delete_error


def _openai_client(*, error=None, files=None):
    response = SimpleNamespace(
        output_text='{"name": "Ada"}',
        status="completed",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
    )
    client = SimpleNamespace(responses=_RecordingEndpoint(response, error))
    client.files = files if files is not None else _RecordingFiles()
    return client


def _anthropic_client(*, error=None, files=None):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"name": "Ada"}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    client = SimpleNamespace(messages=_RecordingEndpoint(response, error))
    beta_messages = _RecordingEndpoint(response, error)
    client.beta = SimpleNamespace(
        files=files if files is not None else _RecordingFiles(),
        messages=beta_messages,
    )
    return client


# Input preparation


def test_large_image_is_downscaled_to_configured_long_edge():
    converted, mime_type = resize_image_bytes(
        _image_bytes(size=(200, 100)),
        "image/png",
        max_long_edge=80,
    )
    with Image.open(BytesIO(converted)) as image:
        assert image.size == (80, 40)
        assert image.mode == "RGB"
        assert image.format == "JPEG"
    assert mime_type == "image/jpeg"


def test_small_image_is_not_upscaled():
    converted, _ = resize_image_bytes(
        _image_bytes(size=(40, 20)),
        "image/png",
        max_long_edge=80,
    )
    with Image.open(BytesIO(converted)) as image:
        assert image.size == (40, 20)


@pytest.mark.parametrize("mode", ["P", "RGBA"])
def test_non_rgb_image_is_converted_to_jpeg(mode):
    converted, _ = resize_image_bytes(_image_bytes(mode=mode), "image/png")
    with Image.open(BytesIO(converted)) as image:
        assert image.mode == "RGB"
        assert image.format == "JPEG"


def test_undecodable_image_raises_taxonomy_error():
    with pytest.raises(DocumentExtractionError) as error:
        resize_image_bytes(b"not an image", "image/png")
    assert error.value.code == IMAGE_CONVERSION_FAILED


def test_pdf_text_is_extracted_and_truncated_to_cap(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda stream: SimpleNamespace(pages=[Page("abc"), Page("defgh")]),
    )
    assert extract_pdf_text(b"%PDF-fake", character_cap=7) == "abc\ndef"


def test_unparseable_pdf_returns_empty_string():
    assert extract_pdf_text(b"not a PDF") == ""


# Response salvage


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ('{"name": "Ada"}', {"name": "Ada"}),
        ('```json\n{"name": "Ada"}\n```', {"name": "Ada"}),
        ('```\n{"name": "Ada"}\n```', {"name": "Ada"}),
        ('Before the payload {"name": "Ada"} after it.', {"name": "Ada"}),
        ('{"payload": {"name": "Ada"}}', {"name": "Ada"}),
        ('{"data": {"name": "Ada"}}', {"name": "Ada"}),
        ('{"result": {"name": "Ada"}}', {"name": "Ada"}),
        ('{"fields": {"name": "Ada"}}', {"name": "Ada"}),
        ('[{"name": "Ada"}, {"name": "Grace"}]', {"name": "Ada"}),
    ],
)
def test_json_payload_real_world_text_shapes(raw_text, expected):
    assert extract_json_payload(raw_text) == expected


def test_json_payload_is_recovered_from_tool_call_arguments():
    response = {
        "output": [
            {
                "type": "tool_call",
                "arguments": json.dumps({"programme_title": "Camp"}),
            }
        ]
    }
    assert extract_json_payload("", response) == {"programme_title": "Camp"}


def test_usage_block_is_never_mistaken_for_the_payload():
    # Regression: the fallback search must never surface a provider's token-usage
    # metadata as the extracted payload, regardless of where "usage" sits in the
    # response object's field order.
    response = {
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "output": [
            {
                "type": "tool_call",
                "arguments": json.dumps({"programme_title": "Camp"}),
            }
        ],
    }
    assert extract_json_payload("", response) == {"programme_title": "Camp"}


def test_nothing_parseable_returns_empty_payload():
    assert extract_json_payload("plain prose", {"type": "message"}) == {}


# Cost guard: migrated behavior and provider-neutral additions


def test_estimate_known_model_uses_table_rates():
    assert estimate_cost_cents(
        model="gpt-4o-mini", input_tokens=1000, output_tokens=1000
    ) == 1


def test_estimate_claude_model_uses_own_list_price():
    assert estimate_cost_cents(
        model="claude-sonnet-5", input_tokens=1000, output_tokens=1000
    ) == 2


def test_estimate_unknown_model_uses_conservative_fallback():
    assert estimate_cost_cents(
        model="unknown-model", input_tokens=1000, output_tokens=1000
    ) == 8


def test_estimate_minimum_one_cent_and_scales():
    assert estimate_cost_cents(
        model="gpt-4o-mini", input_tokens=0, output_tokens=0
    ) == 1
    assert estimate_cost_cents(
        model="gpt-4o", input_tokens=100_000, output_tokens=100_000
    ) > estimate_cost_cents(
        model="gpt-4o", input_tokens=1000, output_tokens=1000
    )


@override_settings(AI_COST_LIMIT_ENABLED=False)
def test_charge_user_noop_when_disabled():
    assert charge_user(user_id=1, cents=99_999) == 0


@override_settings(IS_LOCAL=True)
def test_cap_defaults_disabled_locally():
    assert charge_user(user_id=1, cents=99_999) == 0


@override_settings(IS_LOCAL=False, AI_DAILY_COST_LIMIT_CENTS=1)
def test_cap_defaults_enabled_outside_local():
    with pytest.raises(AICostLimitExceeded):
        charge_user(user_id=1, cents=2)


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_charge_user_noops_for_missing_user_or_nonpositive_charge():
    assert charge_user(user_id=None, cents=100) == 0
    assert charge_user(user_id=1, cents=0) == 0
    assert charge_user(user_id=1, cents=-50) == 0


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_charge_user_increments_and_isolates_users():
    assert charge_user(user_id=42, cents=100) == 100
    assert charge_user(user_id=42, cents=50) == 150
    assert current_user_spend_cents(42) == 150
    assert charge_user(user_id=43, cents=400) == 400
    assert current_user_spend_cents(43) == 400


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_charge_user_persists_over_limit_total_and_blocks_next_request():
    charge_user(user_id=7, cents=499)
    with pytest.raises(AICostLimitExceeded) as error:
        charge_user(user_id=7, cents=5)
    assert (error.value.user_id, error.value.used_cents, error.value.limit_cents) == (
        7,
        504,
        500,
    )
    with pytest.raises(AICostLimitExceeded):
        charge_user(user_id=7, cents=1)


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_charge_user_for_response_extracts_usage():
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=1000)
    )
    assert charge_user_for_response(
        user_id=11, response=response, model="gpt-4o-mini"
    ) == 1


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
@pytest.mark.parametrize(
    ("model", "expected"),
    [("gpt-4o-mini", 1), ("unknown-model", 8)],
)
def test_missing_usage_still_charges(model, expected):
    assert charge_user_for_response(
        user_id=12, response=SimpleNamespace(), model=model
    ) == expected


def test_cache_key_is_provider_neutral_and_scoped():
    key = _cache_key(1234)
    assert "ai:cost:1234" in key
    suffix = key.rsplit(":", 1)[-1]
    assert suffix.isdigit() and len(suffix) == 8


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_first_charge_seeds_and_second_charge_increments():
    assert charge_user(user_id=100, cents=30) == 30
    assert charge_user(user_id=100, cents=20) == 50


# Schema contract


def test_schema_rejects_additional_properties_true_at_any_depth():
    schema = _valid_schema()
    schema["properties"]["details"]["additionalProperties"] = True
    with pytest.raises(DocumentExtractionError) as error:
        validate_schema(schema)
    assert error.value.code == INVALID_JSON
    assert "additionalProperties must be false" in error.value.message


def test_schema_rejects_type_arrays():
    schema = _valid_schema()
    schema["properties"]["name"]["type"] = ["string", "null"]
    with pytest.raises(DocumentExtractionError) as error:
        validate_schema(schema)
    assert "type arrays are not allowed" in error.value.message


def test_schema_contract_accepts_intersection_schema():
    validate_schema(_valid_schema())


@pytest.mark.parametrize(
    ("driver", "client_factory"),
    [(openai_driver, _openai_client), (anthropic_driver, _anthropic_client)],
)
def test_schema_is_validated_before_either_provider_request(driver, client_factory):
    request = _request()
    request.schema["additionalProperties"] = True
    client = client_factory()
    with pytest.raises(DocumentExtractionError):
        driver.extract(request, api_key="key", model="model", client=client)
    endpoint = getattr(client, "responses", None) or client.messages
    assert endpoint.calls == []


# Thin drivers


def test_openai_driver_maps_normalized_request_fields():
    client = _openai_client()
    openai_driver.extract(
        _request(image=True),
        api_key="key",
        model="gpt-4.1-mini",
        client=client,
        temperature=0,
    )
    call = client.responses.calls[0]
    assert call["reasoning"]["effort"] == "low"
    assert call["max_output_tokens"] == 321
    assert call["temperature"] == 0
    image = call["input"][0]["content"][1]
    assert image["type"] == "input_image"
    assert image["image_url"].startswith("data:image/png;base64,")


def test_anthropic_driver_maps_normalized_request_fields_and_explicit_thinking():
    client = _anthropic_client()
    anthropic_driver.extract(
        _request(image=True),
        api_key="key",
        model="claude-sonnet-5",
        client=client,
        temperature=0,
    )
    call = client.messages.calls[0]
    assert call["output_config"]["effort"] == "low"
    assert call["max_tokens"] == 321
    assert call["thinking"] == {"type": "disabled"}
    assert call["temperature"] == 0
    image = call["messages"][0]["content"][0]
    assert image == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": image["source"]["data"],
        },
    }


@pytest.mark.parametrize(
    ("driver", "field"),
    [(openai_driver, "max_output_tokens"), (anthropic_driver, "max_tokens")],
)
def test_passthrough_collision_with_normalized_field_raises_taxonomy_error(driver, field):
    client = _openai_client() if driver is openai_driver else _anthropic_client()
    with pytest.raises(DocumentExtractionError) as error:
        driver.extract(
            _request(),
            api_key="key",
            model="model",
            client=client,
            **{field: 1},
        )
    assert error.value.code == REQUEST_FAILED
    endpoint = getattr(client, "responses", None) or client.messages
    assert endpoint.calls == []


@pytest.mark.parametrize(
    ("driver", "client"),
    [
        (openai_driver, _openai_client(error=_ProviderFailure())),
        (anthropic_driver, _anthropic_client(error=_ProviderFailure())),
    ],
)
def test_provider_error_preserves_status_code(driver, client):
    with pytest.raises(DocumentExtractionError) as error:
        driver.extract(_request(), api_key="key", model="model", client=client)
    assert error.value.code == REQUEST_FAILED
    assert error.value.status_code == 429


# Raw-file upload path (AI-5)


def test_extraction_request_rejects_upload_without_mime_or_filename():
    with pytest.raises(ValueError):
        ExtractionRequest(
            system_prompt="s",
            user_prompt="u",
            schema=_valid_schema(),
            max_output_tokens=10,
            effort="low",
            thinking={"type": "disabled"},
            upload_bytes=b"bytes",
        )


def test_extraction_request_rejects_image_and_upload_together():
    with pytest.raises(ValueError):
        ExtractionRequest(
            system_prompt="s",
            user_prompt="u",
            schema=_valid_schema(),
            max_output_tokens=10,
            effort="low",
            thinking={"type": "disabled"},
            image_bytes=_image_bytes(),
            image_mime_type="image/png",
            upload_bytes=b"bytes",
            upload_mime_type="application/pdf",
            upload_filename="scan.pdf",
        )


def test_openai_driver_uploads_and_references_file():
    client = _openai_client()
    openai_driver.extract(
        _request(upload_mime="application/pdf"),
        api_key="key",
        model="gpt-4.1-mini",
        client=client,
    )
    assert client.files.create_calls[0] == "user_data"
    call = client.responses.calls[0]
    file_block = call["input"][0]["content"][1]
    assert file_block == {"type": "input_file", "file_id": client.files.file_id}
    assert client.files.delete_calls == [client.files.file_id]


def test_openai_driver_deletes_upload_after_failed_request():
    client = _openai_client(error=_ProviderFailure())
    with pytest.raises(DocumentExtractionError):
        openai_driver.extract(
            _request(upload_mime="application/pdf"),
            api_key="key",
            model="model",
            client=client,
        )
    assert client.files.delete_calls == [client.files.file_id]


def test_openai_driver_swallows_delete_failure_and_still_returns_result():
    client = _openai_client(files=_RecordingFiles(delete_error=RuntimeError("gone")))
    result = openai_driver.extract(
        _request(upload_mime="application/pdf"),
        api_key="key",
        model="model",
        client=client,
    )
    assert result.raw_text == '{"name": "Ada"}'


def test_openai_driver_falls_back_to_second_purpose_on_upload_rejection():
    client = _openai_client(files=_RecordingFiles(purpose_sequence={"user_data"}))
    openai_driver.extract(
        _request(upload_mime="image/jpeg"),
        api_key="key",
        model="model",
        client=client,
    )
    assert client.files.create_calls == ["user_data", "assistants"]


def test_anthropic_driver_uploads_pdf_as_document_block_via_beta_messages():
    client = _anthropic_client()
    anthropic_driver.extract(
        _request(upload_mime="application/pdf"),
        api_key="key",
        model="claude-sonnet-5",
        client=client,
    )
    assert client.beta.files.upload_calls
    call = client.beta.messages.calls[0]
    assert call["betas"] == ["files-api-2025-04-14"]
    file_block = call["messages"][0]["content"][0]
    assert file_block == {
        "type": "document",
        "source": {"type": "file", "file_id": client.beta.files.file_id},
    }
    assert client.beta.files.delete_calls == [client.beta.files.file_id]
    # The non-upload stable endpoint must never be touched for this path.
    assert client.messages.calls == []


def test_anthropic_driver_uploads_image_as_image_block():
    client = _anthropic_client()
    anthropic_driver.extract(
        _request(upload_mime="image/png"),
        api_key="key",
        model="claude-sonnet-5",
        client=client,
    )
    call = client.beta.messages.calls[0]
    file_block = call["messages"][0]["content"][0]
    assert file_block["type"] == "image"
    assert file_block["source"] == {"type": "file", "file_id": client.beta.files.file_id}


def test_anthropic_driver_deletes_upload_after_failed_request():
    client = _anthropic_client(error=_ProviderFailure())
    with pytest.raises(DocumentExtractionError):
        anthropic_driver.extract(
            _request(upload_mime="application/pdf"),
            api_key="key",
            model="model",
            client=client,
        )
    assert client.beta.files.delete_calls == [client.beta.files.file_id]


def test_anthropic_driver_swallows_delete_failure_and_still_returns_result():
    client = _anthropic_client(files=_RecordingFiles(delete_error=RuntimeError("gone")))
    result = anthropic_driver.extract(
        _request(upload_mime="application/pdf"),
        api_key="key",
        model="model",
        client=client,
    )
    assert result.raw_text == '{"name": "Ada"}'


@pytest.mark.parametrize(
    ("driver", "client_factory"),
    [(openai_driver, _openai_client), (anthropic_driver, _anthropic_client)],
)
def test_schema_is_validated_before_any_upload_call(driver, client_factory):
    request = _request(upload_mime="application/pdf")
    request.schema["additionalProperties"] = True
    client = client_factory()
    with pytest.raises(DocumentExtractionError):
        driver.extract(request, api_key="key", model="model", client=client)
    files = client.files if driver is openai_driver else client.beta.files
    assert files.create_calls == []
    assert files.upload_calls == []
    assert files.delete_calls == []


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_normalized_call_meters_only_after_successful_response():
    client = _openai_client()
    result = extract_document(
        _request(),
        provider="openai",
        api_key="key",
        model="gpt-4o-mini",
        user_id=88,
        client=client,
    )
    assert result.raw_text == '{"name": "Ada"}'
    assert current_user_spend_cents(88) == 1


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_failed_provider_request_is_not_charged():
    with pytest.raises(DocumentExtractionError):
        extract_document(
            _request(),
            provider="openai",
            api_key="key",
            model="gpt-4o-mini",
            user_id=89,
            client=_openai_client(error=_ProviderFailure()),
        )
    assert current_user_spend_cents(89) == 0


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=500)
def test_truncated_provider_response_is_charged_before_error_is_raised():
    response = SimpleNamespace(
        output_text="",
        status="incomplete",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=1000),
    )
    client = SimpleNamespace(responses=_RecordingEndpoint(response))
    with pytest.raises(DocumentExtractionError) as error:
        extract_document(
            _request(),
            provider="openai",
            api_key="key",
            model="gpt-4o-mini",
            user_id=90,
            client=client,
        )
    assert error.value.code == OUTPUT_TRUNCATED
    assert current_user_spend_cents(90) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1)
def test_exhausted_budget_blocks_both_providers_before_client_call(provider):
    charge_user(201, 1)
    client = _openai_client() if provider == "openai" else _anthropic_client()
    for _ in range(3):
        with pytest.raises(AICostLimitExceeded):
            extract_document(
                _request(), provider=provider, api_key="key", model="gpt-4o-mini",
                user_id=201, client=client,
            )
    endpoint = client.responses if provider == "openai" else client.messages
    assert endpoint.calls == []


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1)
def test_outstanding_reservation_blocks_provider_at_exhaustion():
    reservation = reserve_for_extraction(user_id=202, request=_request(), model="gpt-4o-mini")
    client = _openai_client()
    with pytest.raises(AICostLimitExceeded):
        extract_document(
            _request(), provider="openai", api_key="key", model="gpt-4o-mini",
            user_id=202, client=client,
        )
    assert client.responses.calls == []
    release_reservation(reservation=reservation)


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=10)
def test_success_settles_once_and_releases_reservation(monkeypatch):
    import django_core_micha.extraction.service as service

    seen = []
    real_driver = service.openai_driver.extract

    def observing_driver(*args, **kwargs):
        seen.append(sum(
            _read_counter(_reservation_key(203, _today_key(), bucket))
            for bucket in _reservation_buckets(time.time(), 30 * 60)
        ))
        return real_driver(*args, **kwargs)

    monkeypatch.setattr(service.openai_driver, "extract", observing_driver)
    client = _openai_client()
    extract_document(
        _request(), provider="openai", api_key="key", model="gpt-4o-mini",
        user_id=203, client=client,
    )
    assert len(client.responses.calls) == 1
    assert seen and seen[0] > 0  # admitted BEFORE the provider ran
    assert current_user_spend_cents(203) == 1
    now = time.time()
    outstanding = sum(
        _read_counter(_reservation_key(203, _today_key(), bucket))
        for bucket in _reservation_buckets(now, 30 * 60)
    )
    assert outstanding == 0


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1)
def test_definitively_unbilled_provider_error_releases_reservation(monkeypatch):
    import django_core_micha.extraction.service as service

    seen = []
    real_driver = service.openai_driver.extract

    def observing_driver(*args, **kwargs):
        seen.append(sum(
            _read_counter(_reservation_key(204, _today_key(), bucket))
            for bucket in _reservation_buckets(time.time(), 30 * 60)
        ))
        return real_driver(*args, **kwargs)

    monkeypatch.setattr(service.openai_driver, "extract", observing_driver)
    error = Exception("bad request")
    error.status_code = 400
    with pytest.raises(DocumentExtractionError):
        extract_document(
            _request(), provider="openai", api_key="key", model="gpt-4o-mini",
            user_id=204, client=_openai_client(error=error),
        )
    assert seen and seen[0] > 0  # reserved while the provider ran...
    assert sum(
        _read_counter(_reservation_key(204, _today_key(), bucket))
        for bucket in _reservation_buckets(time.time(), 30 * 60)
    ) == 0  # ...and released afterwards
    assert current_user_spend_cents(204) == 0
    client = _openai_client()
    extract_document(
        _request(), provider="openai", api_key="key", model="gpt-4o-mini",
        user_id=204, client=client,
    )
    assert len(client.responses.calls) == 1


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=1)
def test_ambiguous_provider_error_keeps_reservation_and_blocks_retries():
    client = _openai_client(error=_ProviderFailure())
    with pytest.raises(DocumentExtractionError):
        extract_document(
            _request(), provider="openai", api_key="key", model="gpt-4o-mini",
            user_id=205, client=client,
        )
    assert len(client.responses.calls) == 1
    with pytest.raises(AICostLimitExceeded):
        extract_document(
            _request(), provider="openai", api_key="key", model="gpt-4o-mini",
            user_id=205, client=_openai_client(),
        )
    client = _openai_client()
    with pytest.raises(AICostLimitExceeded):
        extract_document(
            _request(), provider="openai", api_key="key", model="gpt-4o-mini",
            user_id=205, client=client,
        )
    assert client.responses.calls == []


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=10)
def test_reservation_expiry_releases_abandoned_allowance(monkeypatch):
    import django_core_micha.extraction.cost_guard as cost_guard

    now = [1_800_000_000.0]
    monkeypatch.setattr(cost_guard.time, "time", lambda: now[0])
    reservation = reserve_for_extraction(user_id=206, request=_request(), model="gpt-4o-mini")
    key = _reservation_key(reservation.user_id, reservation.day, reservation.bucket)
    assert _read_counter(key) == reservation.cents
    # Advance the patched clock past the reservation lifetime (LocMemCache reads
    # the same time.time): the abandoned reservation expires on its own.
    now[0] += 3600
    assert _read_counter(key) == 0
    assert reserve_for_extraction(user_id=206, request=_request(), model="gpt-4o-mini")


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=10)
def test_midnight_settlement_uses_admission_day(monkeypatch):
    import django_core_micha.extraction.cost_guard as cost_guard

    days = ["20261011", "20261012"]
    monkeypatch.setattr(cost_guard, "_today_key", lambda: days[0])
    reservation = reserve_for_extraction(user_id=207, request=_request(), model="gpt-4o-mini")
    days[0] = days[1]
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    settle_reservation(reservation=reservation, response=response, model="gpt-4o-mini")
    assert cache.get(_cache_key_for_day(207, "20261011")) == 1
    assert cache.get(_cache_key_for_day(207, "20261012"), 0) == 0


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=10, ENV_TYPE="test-a")
def test_budget_is_isolated_by_environment_and_existing_rollout_usage():
    charge_user(208, 10)
    with pytest.raises(AICostLimitExceeded):
        reserve_for_extraction(user_id=208, request=_request(), model="gpt-4o-mini")
    with override_settings(ENV_TYPE="test-b"):
        reservation = reserve_for_extraction(user_id=208, request=_request(), model="gpt-4o-mini")
        release_reservation(reservation=reservation)
    assert current_user_spend_cents(208) == 10
    assert current_user_spend_cents(209) == 0


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=3)
def test_concurrent_admission_uses_real_atomic_cache_and_limits_provider_calls(monkeypatch):
    import django_core_micha.extraction.service as service

    workers = 6
    ready = threading.Barrier(workers)
    release_provider = threading.Event()
    calls = []

    def blocked_provider(*args, **kwargs):
        calls.append(1)
        assert release_provider.wait(5)
        return ExtractionResult("{}", {}, SimpleNamespace(input_tokens=10, output_tokens=5))

    monkeypatch.setattr(service.openai_driver, "extract", blocked_provider)

    def worker():
        ready.wait(timeout=5)
        try:
            service.extract_document(
                _request(), provider="openai", api_key="key", model="gpt-4o-mini",
                user_id=209, client=object(),
            )
            return "admitted"
        except AICostLimitExceeded:
            return "denied"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        # Denied workers finish at once; at most 3 can ever be admitted (blocked).
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and sum(f.done() for f in futures) < workers - 3:
            time.sleep(0.01)
        # Admission is conservative under contention: concurrent requests may deny
        # each other, but the reserved total never exceeds the cap.
        outstanding = sum(
            _read_counter(_reservation_key(209, _today_key(), bucket))
            for bucket in _reservation_buckets(time.time(), 30 * 60)
        )
        assert outstanding <= 3
        release_provider.set()
        outcomes = [future.result(timeout=5) for future in futures]
    admitted = outcomes.count("admitted")
    assert admitted <= 3
    assert len(calls) == admitted
    assert outcomes.count("denied") == workers - admitted
    assert current_user_spend_cents(209) == admitted


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=3)
def test_sequential_admission_admits_exactly_the_affordable_count():
    reservations = [
        reserve_for_extraction(user_id=211, request=_request(), model="gpt-4o-mini")
        for _ in range(3)
    ]
    assert all(reservations)
    with pytest.raises(AICostLimitExceeded):
        reserve_for_extraction(user_id=211, request=_request(), model="gpt-4o-mini")
    # The denied attempt rolled itself back: releasing one makes room for one.
    release_reservation(reservation=reservations[0])
    assert reserve_for_extraction(user_id=211, request=_request(), model="gpt-4o-mini")


@override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=10)
def test_cache_failure_blocks_provider_before_call(monkeypatch):
    monkeypatch.setattr(cache, "add", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cache down")))
    client = _openai_client()
    with pytest.raises(AICostAccountingUnavailable):
        extract_document(
            _request(), provider="openai", api_key="key", model="gpt-4o-mini",
            user_id=210, client=client,
        )
    assert client.responses.calls == []
