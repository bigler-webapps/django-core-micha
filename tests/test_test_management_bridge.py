import json

import pytest

from django_core_micha.test_management.local_bridge import dispatch


class FakeConnection:
    calls = []

    def __init__(self, host, port, timeout):
        self.calls.append((host, port, timeout))

    def request(self, method, path, body, headers):
        self.calls.append((method, path, body, headers))

    def getresponse(self):
        class Response:
            status = 302

            def read(self, size):
                return b'{"code":"redirect_refused"}'

        return Response()

    def close(self):
        pass


def test_bridge_keeps_credential_inside_loopback_request():
    FakeConnection.calls.clear()
    token = "synthetic-" + "x" * 32
    result = dispatch({"method": "GET", "path": "/api/test-management/discovery/"}, token, FakeConnection)
    assert FakeConnection.calls[0][:2] == ("127.0.0.1", 8000)
    assert FakeConnection.calls[1][3]["Authorization"] == f"Bearer {token}"
    assert result == {"status": 302, "body": {"code": "redirect_refused"}}
    assert token not in json.dumps(result)


def test_bridge_redacts_credentials_even_if_backend_echoes_them():
    token = "synthetic-" + "x" * 32
    password = "synthetic-private-password"

    class EchoConnection(FakeConnection):
        def getresponse(self):
            class Response:
                status = 400

                def read(self, size):
                    return json.dumps({"detail": f"{password} {token}", "password": password}).encode()

            return Response()

    response = dispatch({"method": "POST", "path": "/api/test-management/runs/", "body": {"password": password}}, token, EchoConnection)
    assert password not in json.dumps(response)
    assert token not in json.dumps(response)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/test-management/discovery/"),
    ("GET", "http://example.test/api/test-management/discovery/"),
    ("GET", "/api/test-management/discovery/?token=secret"),
    ("GET", "/api/test-management/../../api/users/"),
    ([], "/api/test-management/discovery/"),
])
def test_bridge_rejects_unadvertised_routes_before_connecting(method, path):
    FakeConnection.calls.clear()
    assert dispatch({"method": method, "path": path}, "x" * 32, FakeConnection)["status"] == 400
    assert FakeConnection.calls == []
