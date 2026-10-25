import json
import socket
import threading
import time

from django_core_micha.assistant import cli_bridge


def _call_against_fake_driver(tmp_path, monkeypatch, response, chunks):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()
    config_path = tmp_path / "bridge-config.json"
    config_path.write_text(
        json.dumps({"endpoint": f"tcp:{host}:{port}", "nonce": "test-nonce"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DCM_ASSISTANT_BRIDGE_CONFIG", str(config_path))

    def serve():
        try:
            conn, _ = listener.accept()
            with conn:
                request = bytearray()
                while b"\n" not in request:
                    part = conn.recv(4096)
                    if not part:
                        return
                    request.extend(part)
                for size, pause in chunks:
                    if not response:
                        break
                    piece, response[:] = response[:size], response[size:]
                    if piece:
                        conn.sendall(piece)
                    if pause:
                        time.sleep(pause)
                if response:
                    conn.sendall(response)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = cli_bridge._call("lookup", {})
    finally:
        thread.join(timeout=5)
    assert not thread.is_alive()
    return result


def test_reads_complete_large_answer_line(tmp_path, monkeypatch):
    expected = {"status": "ok", "text": "x" * 350_000}
    response = bytearray((json.dumps(expected) + "\n").encode())

    result = _call_against_fake_driver(
        tmp_path,
        monkeypatch,
        response,
        [(32_000, 0.02)] * 4,
    )

    assert result == expected


def test_answer_above_limit_has_distinct_error(tmp_path, monkeypatch):
    expected = {"status": "ok", "text": "x" * (cli_bridge.MAX_ANSWER_BYTES + 10)}
    response = bytearray((json.dumps(expected) + "\n").encode())

    result = _call_against_fake_driver(tmp_path, monkeypatch, response, [(64_000, 0)])

    assert result == {"status": "error", "text": "tool_answer_too_large"}


def test_closed_connection_without_newline_is_unavailable(tmp_path, monkeypatch):
    response = bytearray(b'{"status":"ok","text":"partial')

    result = _call_against_fake_driver(tmp_path, monkeypatch, response, [(len(response), 0)])

    assert result == {"status": "error", "text": "tool_unavailable"}


def test_a_slow_drip_without_newline_ends_at_the_call_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_bridge, "CALL_TIMEOUT_SECONDS", 1)
    response = bytearray(b'{"status":"ok","text":"' + b"x" * 40)
    started = time.monotonic()

    result = _call_against_fake_driver(tmp_path, monkeypatch, response, [(4, 0.3)] * 10)

    assert result == {"status": "error", "text": "tool_unavailable"}
    assert time.monotonic() - started < 2.5  # the per-recv timeout alone would allow ~3 s here
