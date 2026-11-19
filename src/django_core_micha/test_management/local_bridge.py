"""Private stdin/stdout bridge to a controller on the container's loopback.

The local caller supplies JSON on stdin. The bearer credential stays in the
container environment and never appears in command arguments or output.
"""

import http.client
import json
import os
import re
import sys

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ROUTES = (
    (re.compile(r"^/api/test-management/discovery/$"), {"GET"}),
    (re.compile(r"^/api/test-management/runs/$"), {"POST"}),
    (re.compile(rf"^/api/test-management/runs/{_UUID}/$"), {"GET"}),
    (re.compile(rf"^/api/test-management/runs/{_UUID}/(?:recover|reset|close)/$"), {"POST"}),
    (re.compile(rf"^/api/test-management/runs/{_UUID}/accounts/$"), {"GET", "POST"}),
    (re.compile(rf"^/api/test-management/runs/{_UUID}/accounts/{_UUID}/$"), {"GET", "PATCH", "DELETE"}),
    (re.compile(rf"^/api/test-management/runs/{_UUID}/accounts/{_UUID}/recovery-message/$"), {"POST"}),
    (re.compile(rf"^/api/test-management/runs/{_UUID}/fixtures/$"), {"POST"}),
)
_MAX_BYTES = 1024 * 1024


def _redact(value, secrets):
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()
                if not re.search(r"password|token|cookie|authorization|secret", str(key), re.I)}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


def dispatch(message, token, connection_factory=http.client.HTTPConnection):
    if not isinstance(message, dict) or set(message) - {"method", "path", "body"}:
        return {"status": 400, "body": {"code": "invalid_request"}}
    method, path = message.get("method"), message.get("path")
    if not isinstance(method, str) or not isinstance(path, str) or not any(pattern.fullmatch(path) and method in methods for pattern, methods in _ROUTES):
        return {"status": 400, "body": {"code": "invalid_route"}}
    if not isinstance(token, str) or len(token) < 32 or "\r" in token or "\n" in token:
        return {"status": 503, "body": {"code": "controller_unavailable"}}
    body = json.dumps(message.get("body", {}), separators=(",", ":")).encode("utf-8")
    if len(body) > _MAX_BYTES:
        return {"status": 400, "body": {"code": "request_too_large"}}
    connection = connection_factory("127.0.0.1", 8000, timeout=15)
    try:
        connection.request(method, path, body=body if method in {"POST", "PATCH"} else None,
                           headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        payload = response.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            return {"status": 502, "body": {"code": "response_too_large"}}
        if response.status == 204:
            return {"status": 204, "body": None}
        parsed = json.loads(payload)
        supplied_password = message.get("body", {}).get("password", "") if isinstance(message.get("body"), dict) else ""
        return {"status": response.status, "body": _redact(parsed, [token, supplied_password if isinstance(supplied_password, str) else ""])}
    except (OSError, ValueError, http.client.HTTPException):
        return {"status": 503, "body": {"code": "controller_unavailable"}}
    finally:
        connection.close()


def main():
    try:
        raw = sys.stdin.buffer.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("request too large")
        message = json.loads(raw)
        result = dispatch(message, os.environ.get("TEST_MANAGEMENT_CONTROLLER_TOKEN", ""))
    except (ValueError, TypeError, UnicodeError):
        result = {"status": 400, "body": {"code": "invalid_request"}}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
