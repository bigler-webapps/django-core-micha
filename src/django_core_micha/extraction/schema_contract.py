"""Validation for the cross-provider JSON-schema subset."""

from __future__ import annotations

from typing import Any

from .errors import DocumentExtractionError, INVALID_JSON


def validate_schema(schema: dict) -> None:
    """Validate the schema subset accepted by both supported providers."""
    if not isinstance(schema, dict):
        raise DocumentExtractionError(INVALID_JSON, "Schema must be a JSON object.")
    _validate_node(schema, path="$")


def _validate_node(node: Any, *, path: str) -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _validate_node(item, path=f"{path}[{index}]")
        return
    if not isinstance(node, dict):
        return

    schema_type = node.get("type")
    if isinstance(schema_type, list):
        raise DocumentExtractionError(
            INVALID_JSON,
            f"Schema contract violation at {path}: JSON-Schema type arrays are not allowed.",
        )
    if schema_type == "object" and node.get("additionalProperties") is not False:
        raise DocumentExtractionError(
            INVALID_JSON,
            f"Schema contract violation at {path}: additionalProperties must be false on every object.",
        )

    for key, value in node.items():
        _validate_node(value, path=f"{path}.{key}")
