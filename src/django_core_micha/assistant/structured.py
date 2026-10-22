"""Schema checks and value validation for one-shot structured completions."""

import json
import math

from django_core_micha.extraction.schema_contract import validate_schema

_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const",
             "anyOf", "description", "title"}
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def check_schema(schema):
    """Refuse schemas that cannot be enforced consistently by every completion driver."""
    try:
        validate_schema(schema)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            return False

        def check(node):
            if not isinstance(node, dict) or set(node) - _KEYWORDS:
                return False
            kind = node.get("type")
            if kind is not None and kind not in _TYPES:
                return False
            if kind is None and "anyOf" not in node:
                return False  # an untyped node is neither strict-mode expressible nor enforceable here
            if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
                return False
            if any(key in node and not isinstance(node[key], str) for key in ("description", "title")):
                return False
            if "items" in node and not isinstance(node["items"], dict):
                return False
            if kind == "object":
                props = node.get("properties")
                required = node.get("required")
                if not isinstance(props, dict) or node.get("additionalProperties") is not False:
                    return False
                if not isinstance(required, list) or set(required) != set(props) or len(required) != len(props):
                    return False
                if any(not isinstance(k, str) or not check(v) for k, v in props.items()):
                    return False
            if kind == "array" and ("items" not in node or not check(node["items"])):
                return False
            if "items" in node and not check(node["items"]):
                return False
            if "properties" in node and kind != "object":
                return False
            if "required" in node and kind != "object":
                return False
            if "anyOf" in node and (not isinstance(node["anyOf"], list) or not node["anyOf"] or
                                      any(not check(option) for option in node["anyOf"])):
                return False
            return True

        return check(schema)
    except Exception:
        return False


def validation_error(value, schema):
    """Return a bounded path-aware error when value does not satisfy the schema."""
    def fail(path, message):
        return f"{path}: {message}"[:300]

    def same(a, b):
        # JSON equality: true is not 1, containers compare element-wise
        if isinstance(a, bool) or isinstance(b, bool):
            return type(a) is type(b) and a == b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a == b
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
        return type(a) is type(b) and a == b

    def check(item, rule, path):
        # anyOf is one constraint among the node's others, never a shortcut past them
        if "anyOf" in rule and not any(check(item, option, path) is None for option in rule["anyOf"]):
            return fail(path, "does not match any allowed type")
        if "const" in rule and not same(item, rule["const"]):
            return fail(path, "does not equal the required value")
        if "enum" in rule and not any(same(item, option) for option in rule["enum"]):
            return fail(path, "is not an allowed value")
        kind = rule.get("type")
        matches = {
            "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None,
        }
        if kind and not matches[kind](item):
            return fail(path, f"must be {kind}")
        if kind == "object":
            props = rule.get("properties", {})
            for key in rule.get("required", []):
                if key not in item:
                    return fail(f"{path}.{key}", "is required")
            for key, child in item.items():
                if key not in props:
                    return fail(f"{path}.{key}", "is not allowed")
                error = check(child, props[key], f"{path}.{key}")
                if error:
                    return error
        if kind == "array":
            for index, child in enumerate(item):
                error = check(child, rule["items"], f"{path}[{index}]")
                if error:
                    return error
        return None

    try:
        return check(value, schema, "$")
    except Exception:
        return "$: value does not match the required schema"
