"""Small, bounded enrollment transport with an injectable test seam."""

import json
import urllib.error
import urllib.request
from urllib.parse import urljoin

from django.conf import settings


class EnrollmentTransport:
    def __init__(self, *, opener=None, timeout=None):
        self.opener = opener or urllib.request.build_opener(_NoRedirect())
        self.timeout = timeout or max(1, min(30, int(getattr(settings, "DCM_AI_POLICY_TIMEOUT", 5))))

    def _url(self, path):
        base = str(getattr(settings, "DCM_AI_POLICY_BASE_URL", "") or "").rstrip("/") + "/"
        if not base.lower().startswith("https://"):
            raise RuntimeError("invalid_policy_transport")
        return urljoin(base, path.lstrip("/"))

    def _request(self, method, path, body=None):
        credential = getattr(settings, "DCM_AI_POLICY_ENROLLMENT_CREDENTIAL", "") or ""
        if not credential:
            raise RuntimeError("policy_not_configured")
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self._url(path), data=data, method=method,
            headers={"Authorization": f"Bearer {credential}", "Accept": "application/json",
                     **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if getattr(response, "geturl", lambda: request.full_url)() != request.full_url:
                    raise RuntimeError("invalid_policy_redirect")
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise RuntimeError("policy_response_too_large")
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("policy_transport_error") from exc

    def get(self):
        return self._request("GET", "/api/ai-policy/enrollment/")

    def applied(self, revision):
        return self._request("POST", "/api/ai-policy/enrollment/applied/", {"revision": revision})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


PolicyTransport = EnrollmentTransport

