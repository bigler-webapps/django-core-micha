from __future__ import annotations

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from rest_framework.request import Request

from django_core_micha.auth._audit_helpers import _client_ip
from django_core_micha.auth.adapters import CoreAccountAdapter
from django_core_micha.auth.client_ip import resolve_client_ip
from django_core_micha.auth.throttles import (
    CoreAnonRateThrottle,
    CoreScopedRateThrottle,
    CoreUserRateThrottle,
)


factory = RequestFactory()


def _request(**meta):
    headers = {"REMOTE_ADDR": "198.51.100.7", **meta}
    request = factory.get("/", **headers)
    return Request(request)


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"HTTP_CF_CONNECTING_IP": " 203.0.113.9 "}, "203.0.113.9"),
        ({}, "198.51.100.7"),
        ({"HTTP_CF_CONNECTING_IP": "invalid"}, "198.51.100.7"),
        ({"HTTP_CF_CONNECTING_IP": "2001:0db8:0:0::1"}, "2001:db8::1"),
        (
            {
                "HTTP_CF_CONNECTING_IP": "203.0.113.9",
                "HTTP_X_FORWARDED_FOR": "192.0.2.44",
                "HTTP_X_REAL_IP": "192.0.2.55",
            },
            "203.0.113.9",
        ),
        (
            {
                "HTTP_X_FORWARDED_FOR": "192.0.2.44",
                "HTTP_X_REAL_IP": "192.0.2.55",
            },
            "198.51.100.7",
        ),
    ],
)
def test_resolve_client_ip(meta, expected):
    assert resolve_client_ip(_request(**meta)) == expected


def test_resolve_client_ip_returns_none_without_valid_address():
    request = Request(factory.get("/", REMOTE_ADDR="invalid"))
    assert resolve_client_ip(request) is None


class TinyAnonThrottle(CoreAnonRateThrottle):
    THROTTLE_RATES = {"anon": "2/min"}


class TinyScopedThrottle(CoreScopedRateThrottle):
    THROTTLE_RATES = {"probe": "2/min"}


class TinyUserThrottle(CoreUserRateThrottle):
    THROTTLE_RATES = {"user": "2/min"}


def test_anon_throttle_budget_uses_cloudflare_address_not_forwarded_for():
    cache.clear()
    first = _request(
        HTTP_CF_CONNECTING_IP="203.0.113.30",
        HTTP_X_FORWARDED_FOR="192.0.2.1",
    )
    rotated = _request(
        HTTP_CF_CONNECTING_IP="203.0.113.30",
        HTTP_X_FORWARDED_FOR="192.0.2.2",
    )
    throttle = TinyAnonThrottle()

    assert throttle.allow_request(first, view=None)
    assert throttle.allow_request(rotated, view=None)
    assert not throttle.allow_request(
        _request(
            HTTP_CF_CONNECTING_IP="203.0.113.30",
            HTTP_X_FORWARDED_FOR="192.0.2.3",
        ),
        view=None,
    )
    assert throttle.get_ident(first) == "203.0.113.30"
    # Same REMOTE_ADDR, other Cloudflare address: a separate bucket.
    assert throttle.allow_request(
        _request(HTTP_CF_CONNECTING_IP="203.0.113.31"), view=None
    )


def test_anon_throttle_without_cloudflare_uses_remote_addr():
    cache.clear()
    first = _request(
        REMOTE_ADDR="198.51.100.12",
        HTTP_X_FORWARDED_FOR="192.0.2.1",
        HTTP_X_REAL_IP="192.0.2.2",
    )
    changed_headers = _request(
        REMOTE_ADDR="198.51.100.12",
        HTTP_X_FORWARDED_FOR="192.0.2.9",
        HTTP_X_REAL_IP="192.0.2.8",
    )
    throttle = TinyAnonThrottle()

    assert throttle.allow_request(first, view=None)
    assert throttle.allow_request(changed_headers, view=None)
    assert throttle.get_ident(changed_headers) == "198.51.100.12"


def test_core_throttle_classes_share_the_same_client_identity():
    request = _request(
        HTTP_CF_CONNECTING_IP="203.0.113.41",
        HTTP_X_FORWARDED_FOR="192.0.2.1",
    )
    scoped = TinyScopedThrottle()
    scoped.scope = "probe"
    assert scoped.get_ident(request) == TinyAnonThrottle().get_ident(request)
    assert TinyUserThrottle().get_ident(request) == scoped.get_ident(request)


def test_allauth_and_drf_resolve_the_same_client_address():
    request = _request(
        HTTP_CF_CONNECTING_IP="2001:db8::8",
        HTTP_X_FORWARDED_FOR="192.0.2.1",
    )
    assert CoreAccountAdapter().get_client_ip(request) == "2001:db8::8"
    assert TinyAnonThrottle().get_ident(request) == "2001:db8::8"


def test_allauth_rejects_request_without_resolvable_address():
    request = Request(factory.get("/", REMOTE_ADDR="not-an-ip"))
    with pytest.raises(PermissionDenied, match="Unable to determine client IP address"):
        CoreAccountAdapter().get_client_ip(request)


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"HTTP_CF_CONNECTING_IP": "203.0.113.29"}, "203.0.113.0/24"),
        (
            {"HTTP_CF_CONNECTING_IP": "2001:db8:abcd:1234::7"},
            "2001:db8:abcd::/48",
        ),
        (
            {
                "HTTP_CF_CONNECTING_IP": "203.0.113.29",
                "HTTP_X_REAL_IP": "192.0.2.4",
                "HTTP_X_FORWARDED_FOR": "192.0.2.5",
            },
            "203.0.113.0/24",
        ),
        (
            {
                "HTTP_X_REAL_IP": "192.0.2.4",
                "HTTP_X_FORWARDED_FOR": "192.0.2.5",
            },
            "198.51.100.0/24",
        ),
    ],
)
def test_audit_client_ip_truncates_the_shared_resolver_address(meta, expected):
    assert _client_ip(_request(**meta)) == expected


def test_audit_client_ip_resolves_through_the_shared_resolver(monkeypatch):
    from django_core_micha.auth import _audit_helpers

    monkeypatch.setattr(_audit_helpers, "resolve_client_ip", lambda request: "192.0.2.77")
    assert _client_ip(_request(HTTP_CF_CONNECTING_IP="203.0.113.29")) == "192.0.2.0/24"


@pytest.mark.parametrize("meta", [{}, {"HTTP_CF_CONNECTING_IP": "invalid"}])
def test_audit_client_ip_returns_none_without_a_valid_address(meta):
    request = Request(factory.get("/", REMOTE_ADDR="", **meta))
    assert _client_ip(request) is None
