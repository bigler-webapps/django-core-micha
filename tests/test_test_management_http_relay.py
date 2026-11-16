import base64

import pytest

from django_core_micha.test_management.http_relay import allowed_path, relay


@pytest.mark.parametrize("path", [
    "/api/test-management/discovery/", "/api/test%2dmanagement/discovery/",
    "/api/%74est-management/discovery/", "/api/anything/../test-management/runs/",
    "/admin/", "//remote.example/path", "http://remote.example/",
])
def test_browser_relay_never_exposes_controller_or_admin(path):
    assert not allowed_path(path)


def test_browser_relay_preserves_session_headers_but_strips_authorization():
    class FakeConnection:
        calls = []

        def __init__(self, host, port, timeout):
            self.calls.append((host, port))

        def request(self, method, path, body, headers):
            self.calls.append((method, path, body, headers))

        def getresponse(self):
            class Response:
                status = 200

                def read(self, size):
                    return b"synthetic"

                def getheaders(self):
                    return [("Set-Cookie", "synthetic-session"), ("Content-Length", "9")]
            return Response()

        def close(self):
            pass

    result = relay({"method": "POST", "path": "/api/auth/browser/v1/auth/login", "body": base64.b64encode(b"synthetic").decode(),
                    "headers": [["Cookie", "synthetic-cookie"], ["Authorization", "synthetic-controller"]]}, FakeConnection)
    assert FakeConnection.calls[0] == ("127.0.0.1", 8000)
    assert FakeConnection.calls[1][3] == {"Cookie": "synthetic-cookie"}
    assert result["headers"] == [["Set-Cookie", "synthetic-session"]]
