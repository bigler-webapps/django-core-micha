from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler
import socket
import threading

from django_core_micha.test_management.local_proxy import QuietServer


class OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def test_proxy_answers_burst_of_connections():
    with QuietServer(("127.0.0.1", 0), OkHandler) as server:
        assert server.request_queue_size >= 128
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            with ExitStack() as stack:
                # Connect everything before serving, so all connections must fit the listen backlog.
                connections = [
                    stack.enter_context(socket.create_connection(server.server_address, timeout=5))
                    for _ in range(50)
                ]
                thread.start()
                for connection in connections:
                    connection.sendall(b"GET / HTTP/1.0\r\n\r\n")
                for connection in connections:
                    with connection.makefile("rb") as response:
                        assert response.readline().split()[1] == b"200"
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join()


def test_proxy_binds_loopback_and_suppresses_errors(capsys):
    with QuietServer(("127.0.0.1", 0), OkHandler) as server:
        assert server.socket.getsockname()[0] == "127.0.0.1"
        assert server.handle_error(None, None) is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
