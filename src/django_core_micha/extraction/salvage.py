"""Recover structured payloads from imperfect model responses."""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .errors import DocumentExtractionError, EMPTY_PAYLOAD, INVALID_JSON


_WRAPPER_KEYS = ("payload", "data", "result", "fields", "parsed", "json")
# Never descend into these looking for a payload: they are pure metadata
# containers whose own field names (input_tokens, stop_reason, ...) don't
# overlap with _RESPONSE_ENVELOPE_KEYS, so the leaf-dict fallback below would
# otherwise misidentify e.g. a usage block as the extracted payload.
_METADATA_ONLY_KEYS = {"usage"}
_RESPONSE_ENVELOPE_KEYS = {
    "content",
    "id",
    "message",
    "model",
    "output",
    "role",
    "stop_reason",
    "text",
    "type",
    "usage",
}


def make_json_safe(value: Any) -> Any:
    """Recursively coerce SDK response values into JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): make_json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(inner) for inner in value]
    return str(value)


def response_to_data(response: Any) -> Any:
    """Best-effort conversion of a provider SDK response to plain data."""
    if response is None or isinstance(response, (dict, list)):
        return response
    for method_name in ("model_dump", "to_dict"):
        method = getattr(response, method_name, None)
        if method:
            try:
                return make_json_safe(method())
            except Exception:
                continue
    return make_json_safe(response)


def extract_json_payload(raw_text: str, raw_response_obj: Any = None) -> dict:
    """Recover the first plausible payload without relying on domain fields."""
    payload = _parse_text(raw_text)
    if payload:
        return payload
    return _search_response(response_to_data(raw_response_obj))


def require_json_payload(raw_text: str, raw_response_obj: Any = None) -> dict:
    """Recover a payload or raise a stable error instead of returning emptiness."""
    payload = extract_json_payload(raw_text, raw_response_obj)
    if payload:
        return payload
    if raw_text and raw_text.strip():
        raise DocumentExtractionError(
            INVALID_JSON,
            "The AI provider output did not contain valid JSON.",
            502,
        )
    raise DocumentExtractionError(
        EMPTY_PAYLOAD,
        "The AI provider output did not contain a payload.",
        502,
    )


def _parse_text(text: str) -> dict:
    if not text:
        return {}
    candidates = [text.strip()]
    candidates.extend(_extract_codeblock_candidates(text))
    candidate = _extract_json_candidate(text)
    if candidate:
        candidates.append(candidate)

    for value in candidates:
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue
        payload = _coerce_payload_dict(parsed)
        if payload:
            return payload
    for parsed in _iter_json_values_from_text(text):
        payload = _coerce_payload_dict(parsed)
        if payload:
            return payload
    return {}


def _extract_json_candidate(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        return stripped
    for parsed in _iter_json_values_from_text(stripped):
        return json.dumps(parsed)
    return ""


def _iter_json_values_from_text(text: str) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        starts = [position for position in (text.find("{", index), text.find("[", index)) if position >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            parsed, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        yield parsed
        index = start + max(end, 1)


def _extract_codeblock_candidates(text: str) -> list[str]:
    pattern = r"```(?:json)?\s*([\s\S]*?)```"
    return [match.strip() for match in re.findall(pattern, text, flags=re.IGNORECASE)]


def _coerce_payload_dict(parsed: Any) -> dict:
    if isinstance(parsed, dict):
        for wrapper_key in _WRAPPER_KEYS:
            value = parsed.get(wrapper_key)
            if isinstance(value, dict):
                return value
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    return {}


def _search_response(value: Any) -> dict:
    if isinstance(value, str):
        return _parse_text(value)
    if isinstance(value, list):
        for item in value:
            payload = _search_response(item)
            if payload:
                return payload
        return {}
    if not isinstance(value, dict):
        return {}

    for key in _WRAPPER_KEYS:
        if key in value:
            payload = _search_response(value[key])
            if payload:
                return payload
    arguments = value.get("arguments")
    if isinstance(arguments, str):
        payload = _parse_text(arguments)
        if payload:
            return payload
    for key, child in value.items():
        if key in _METADATA_ONLY_KEYS:
            continue
        payload = _search_response(child)
        if payload:
            return payload

    has_nested_value = any(isinstance(child, (dict, list)) for child in value.values())
    keys = {str(key).lower() for key in value}
    if value and not has_nested_value and not keys.issubset(_RESPONSE_ENVELOPE_KEYS):
        return value
    return {}
