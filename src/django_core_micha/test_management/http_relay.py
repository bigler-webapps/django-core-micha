"""Relay one ordinary app request inside an isolated container, without logs."""

import base64
import http.client
import json
import posixpath
import sys
from urllib.parse import unquote, urlsplit

MAX_BYTES = 32 * 1024 * 1024
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


def allowed_path(path):
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return False
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    decoded = parsed.path
    for _ in range(3):
        decoded = unquote(decoded)
        normalized = "/" + posixpath.normpath("/" + decoded.lstrip("/")).lstrip("/")
        if normalized.lower() == "/admin" or normalized.lower().startswith(("/admin/", "/api/test-management")):
            return False
    return True


def relay(message, connection_factory=http.client.HTTPConnection):
    if not isinstance(message, dict) or set(message) != {"method", "path", "headers", "body"}:
        raise ValueError("invalid request")
    method, path, headers = message["method"], message["path"], message["headers"]
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} or not allowed_path(path) or not isinstance(headers, list):
        raise ValueError("invalid request")
    raw = base64.b64decode(message["body"], validate=True)
    if len(raw) > MAX_BYTES:
        raise ValueError("request too large")
    clean_headers = {}
    for item in headers:
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(v, str) for v in item):
            raise ValueError("invalid header")
        key, value = item
        if key.lower() not in HOP_HEADERS | {"content-length", "authorization"}:
            clean_headers[key] = value
    connection = connection_factory("127.0.0.1", 8000, timeout=120)
    try:
        connection.request(method, path, body=raw if raw else None, headers=clean_headers)
        response = connection.getresponse()
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("response too large")
        return {"status": response.status, "headers": [[k, v] for k, v in response.getheaders() if k.lower() not in HOP_HEADERS | {"content-length"}],
                "body": base64.b64encode(data).decode("ascii")}
    finally:
        connection.close()


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES * 2 + 1)
        if len(raw) > MAX_BYTES * 2:
            raise ValueError("request too large")
        result = relay(json.loads(raw))
    except Exception:
        result = {"status": 502, "headers": [], "body": ""}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
