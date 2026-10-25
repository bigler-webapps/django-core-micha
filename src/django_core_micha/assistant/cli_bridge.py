"""Stdlib-only MCP bridge exposing exactly the tools in its private config."""
import json
import os
import socket
import sys
import time

MAX_ANSWER_BYTES = 1_000_000
CALL_TIMEOUT_SECONDS = 30  # the whole call, connect and read together


def _config():
    try:
        with open(os.environ["DCM_ASSISTANT_BRIDGE_CONFIG"], encoding="utf-8") as stream:
            return json.load(stream)
    except Exception:
        return {}


def _tools():
    try:
        rows = _config()["tools"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != {"name", "description", "inputSchema"} for row in rows):
            return []
        return rows
    except Exception:
        return []


def _call(name, arguments):
    config = _config()
    endpoint = str(config.get("endpoint", ""))
    nonce = str(config.get("nonce", ""))
    kind, _, target = endpoint.partition(":")
    try:
        if kind == "unix":
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); conn.settimeout(CALL_TIMEOUT_SECONDS); conn.connect(target)
        elif kind == "tcp":
            host, _, port = target.rpartition(":"); conn = socket.create_connection((host, int(port)), timeout=CALL_TIMEOUT_SECONDS)
        else: return {"status": "error", "text": "tool_unavailable"}
        with conn:
            conn.sendall((json.dumps({"nonce": nonce, "name": name, "arguments": arguments}) + "\n").encode())
            data = bytearray()
            deadline = time.monotonic() + CALL_TIMEOUT_SECONDS
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return {"status": "error", "text": "tool_unavailable"}
                conn.settimeout(left)
                chunk = conn.recv(65_536)
                if not chunk:
                    break
                newline = chunk.find(b"\n")
                if newline >= 0:
                    data.extend(chunk[:newline])
                    if len(data) > MAX_ANSWER_BYTES:
                        return {"status": "error", "text": "tool_answer_too_large"}
                    break
                data.extend(chunk)
                if len(data) > MAX_ANSWER_BYTES:
                    return {"status": "error", "text": "tool_answer_too_large"}
        return json.loads(data.decode())
    except Exception:
        return {"status": "error", "text": "tool_unavailable"}


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n"); sys.stdout.flush()


def main():
    for raw in sys.stdin:
        try: req = json.loads(raw)
        except ValueError: continue
        if not isinstance(req, dict): continue
        ident, method = req.get("id"), req.get("method")
        if method == "initialize":
            params = req.get("params") or {}
            _send({"jsonrpc":"2.0","id":ident,"result":{"protocolVersion":params.get("protocolVersion","2024-11-05"),"capabilities":{"tools":{}},"serverInfo":{"name":"dcm_tools","version":"1"}}})
        elif method == "tools/list": _send({"jsonrpc":"2.0","id":ident,"result":{"tools":_tools()}})
        elif method == "tools/call":
            p = req.get("params") or {}; name = p.get("name", ""); args = p.get("arguments", {})
            offered = {x["name"] for x in _tools()}
            answer = _call(name,args) if name in offered and isinstance(args,dict) else {"status":"error","text":"tool_not_allowed"}
            _send({"jsonrpc":"2.0","id":ident,"result":{"content":[{"type":"text","text":str(answer.get("content",answer.get("text","tool_unavailable")))}],"isError":answer.get("status")!="ok"}})
        elif method == "ping": _send({"jsonrpc":"2.0","id":ident,"result":{}})
        elif ident is not None: _send({"jsonrpc":"2.0","id":ident,"error":{"code":-32601,"message":"method not found"}})


if __name__ == "__main__": main()
