"""Unit tests for `django_core_micha.auth.throttles`.

These verify the body-keyed throttle classes produce the cache keys we expect.
They do NOT exercise the DRF view stack — full integration sits in the
consumer app tests because django-core-micha's own test settings ship without
REST framework installed (see tests/settings.py).

S52 (PerAccessCodeScopedRateThrottle) and the supporting refactor of the
existing PerEmailScopedRateThrottle (extracted shared base
`_BodyKeyedScopedRateThrottle`) are covered here.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache

from django_core_micha.auth.throttles import (
    CoreAnonRateThrottle,
    PerAccessCodeScopedRateThrottle,
    PerEmailScopedRateThrottle,
)


def _fake_request(body: dict | None) -> SimpleNamespace:
    """Construct a request-shaped object exposing only what the throttle reads.

    The throttle classes only touch `request.data`. The DRF Request wrapper is
    intentionally avoided so these tests stay setting-free.
    """
    return SimpleNamespace(data=body, user=AnonymousUser(), META={"REMOTE_ADDR": "192.0.2.1"})


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# PerEmailScopedRateThrottle — regression after the _BodyKeyedScopedRateThrottle
# refactor. Existing behaviour must be preserved.
# --------------------------------------------------------------------------- #


class TestPerEmailScopedRateThrottle:
    def test_returns_none_without_scope(self):
        throttle = PerEmailScopedRateThrottle()
        # scope deliberately not set
        assert throttle.get_cache_key(_fake_request({"email": "u@x"}), view=None) is None

    def test_returns_none_when_email_missing(self):
        throttle = PerEmailScopedRateThrottle()
        throttle.scope = "email_register_request"
        assert throttle.get_cache_key(_fake_request({}), view=None) is None
        assert throttle.get_cache_key(_fake_request(None), view=None) is None

    def test_keys_on_lowercased_email_hash(self):
        throttle = PerEmailScopedRateThrottle()
        throttle.scope = "email_register_request"
        key = throttle.get_cache_key(_fake_request({"email": "  Alice@Example.COM "}), view=None)
        # Normalisation: strip + lowercase before hashing.
        expected_digest = _sha256("alice@example.com")
        assert key is not None
        assert expected_digest in key
        # Format follows DRF's `throttle_<scope>_<ident>`.
        assert key.startswith("throttle_email_register_request_")

    def test_query_params_are_ignored(self):
        """Body only — query_params would let attackers seed a different bucket."""
        throttle = PerEmailScopedRateThrottle()
        throttle.scope = "email_register_request"
        # `data` is empty so the throttle is a no-op for this request even if
        # the URL carried ?email=victim@example.com — the per-IP throttle
        # still applies via its own class.
        assert throttle.get_cache_key(_fake_request({}), view=None) is None

    def test_identifier_field_keys_same_bucket_as_email(self):
        """`mfa_support_help` accepts `identifier` as an email alias — without
        this fallback an attacker would bypass the per-email cap by posting
        `{identifier: victim@x}` with no `email` key.
        """
        throttle = PerEmailScopedRateThrottle()
        throttle.scope = "email_mfa_support_help"
        key_email = throttle.get_cache_key(
            _fake_request({"email": "victim@example.com"}), view=None
        )
        key_identifier = throttle.get_cache_key(
            _fake_request({"identifier": "victim@example.com"}), view=None
        )
        assert key_email is not None
        assert key_email == key_identifier

    def test_email_wins_over_identifier_when_both_present(self):
        """`email` is the canonical field. `identifier` only fills in when
        `email` is absent — matches the view's own
        ``request.data.get("email") or request.data.get("identifier")``.
        """
        throttle = PerEmailScopedRateThrottle()
        throttle.scope = "email_mfa_support_help"
        key = throttle.get_cache_key(
            _fake_request({"email": "real@x.com", "identifier": "decoy@x.com"}),
            view=None,
        )
        assert key is not None
        assert _sha256("real@x.com") in key
        assert _sha256("decoy@x.com") not in key


# --------------------------------------------------------------------------- #
# PerAccessCodeScopedRateThrottle (S52)
# --------------------------------------------------------------------------- #


class TestPerAccessCodeScopedRateThrottle:
    def test_returns_none_without_scope(self):
        throttle = PerAccessCodeScopedRateThrottle()
        assert (
            throttle.get_cache_key(
                _fake_request({"access_code": "ABC123"}), view=None
            )
            is None
        )

    def test_returns_none_when_both_fields_missing(self):
        throttle = PerAccessCodeScopedRateThrottle()
        throttle.scope = "access_code_probe"
        assert throttle.get_cache_key(_fake_request({"email": "x@x"}), view=None) is None
        assert throttle.get_cache_key(_fake_request({}), view=None) is None
        assert throttle.get_cache_key(_fake_request(None), view=None) is None

    def test_keys_on_access_code_field(self):
        """`register_request` uses the body field `access_code`."""
        throttle = PerAccessCodeScopedRateThrottle()
        throttle.scope = "access_code_probe"
        key = throttle.get_cache_key(
            _fake_request({"access_code": "ABC-123"}), view=None
        )
        assert key is not None
        assert _sha256("abc-123") in key
        assert key.startswith("throttle_access_code_probe_")

    def test_keys_on_code_field_fallback(self):
        """`AccessCodeViewSet.validate` uses the body field `code`."""
        throttle = PerAccessCodeScopedRateThrottle()
        throttle.scope = "access_code_probe"
        key = throttle.get_cache_key(
            _fake_request({"code": "ABC-123"}), view=None
        )
        assert key is not None
        # Same digest as if it came in under `access_code` — both call sites
        # bucket into the same throttle scope, which is intentional: an
        # attacker who probes via /access-codes/validate cannot get a fresh
        # bucket by switching to /users/register-request.
        assert _sha256("abc-123") in key

    def test_access_code_wins_over_code_when_both_present(self):
        """`access_code` is the canonical field. `code` is the secondary fallback."""
        throttle = PerAccessCodeScopedRateThrottle()
        throttle.scope = "access_code_probe"
        key = throttle.get_cache_key(
            _fake_request({"access_code": "AAA", "code": "BBB"}), view=None
        )
        assert key is not None
        assert _sha256("aaa") in key
        # And not BBB.
        assert _sha256("bbb") not in key

    def test_normalisation_strips_and_lowercases(self):
        throttle = PerAccessCodeScopedRateThrottle()
        throttle.scope = "access_code_probe"
        key_lower = throttle.get_cache_key(
            _fake_request({"access_code": "abc-123"}), view=None
        )
        key_messy = throttle.get_cache_key(
            _fake_request({"access_code": "  ABC-123 "}), view=None
        )
        assert key_lower == key_messy

    def test_non_string_access_code_ignored(self):
        """Defence against attackers seeding `access_code: null` or other types."""
        throttle = PerAccessCodeScopedRateThrottle()
        throttle.scope = "access_code_probe"
        assert throttle.get_cache_key(
            _fake_request({"access_code": None}), view=None
        ) is None
        assert throttle.get_cache_key(
            _fake_request({"access_code": 12345}), view=None
        ) is None
        assert throttle.get_cache_key(
            _fake_request({"access_code": ""}), view=None
        ) is None


@pytest.mark.parametrize(
    ("throttle_class", "scope", "body", "rate_key"),
    [
        (
            PerAccessCodeScopedRateThrottle,
            "access_code_probe",
            {"code": "Shared-Code"},
            "access_code_probe",
        ),
        (
            PerEmailScopedRateThrottle,
            "email_recovery_login",
            {"email": "Recovery@Example.com"},
            "email_recovery_login",
        ),
    ],
)
def test_assigned_scope_enforces_rate_and_missing_identifier_falls_back_to_ip(
    monkeypatch, throttle_class, scope, body, rate_key
):
    """The explicit target scope survives DRF dispatch and complements IP limits."""
    cache.clear()
    rates = {rate_key: "2/hour", "anon": "2/hour"}
    monkeypatch.setattr(throttle_class, "THROTTLE_RATES", rates)
    monkeypatch.setattr(CoreAnonRateThrottle, "THROTTLE_RATES", rates)

    target = throttle_class()
    target.scope = scope
    assert target.allow_request(_fake_request(body), view=SimpleNamespace(throttle_scope=None))
    assert target.allow_request(_fake_request(body), view=SimpleNamespace(throttle_scope=None))
    assert not target.allow_request(_fake_request(body), view=SimpleNamespace(throttle_scope=None))

    # A missing body key remains a no-op for the target throttle, while the
    # independently configured client/IP throttle still enforces its budget.
    cache.clear()
    missing = _fake_request({})
    assert target.allow_request(missing, view=SimpleNamespace(throttle_scope=None))
    assert target.allow_request(missing, view=SimpleNamespace(throttle_scope=None))
    client_ip = CoreAnonRateThrottle()
    assert client_ip.allow_request(missing, view=None)
    assert client_ip.allow_request(missing, view=None)
    assert not client_ip.allow_request(missing, view=None)


def test_target_throttle_is_optional_for_hosts_without_a_configured_rate(monkeypatch):
    monkeypatch.setattr(PerEmailScopedRateThrottle, "THROTTLE_RATES", {"anon": "2/hour"})
    target = PerEmailScopedRateThrottle()
    target.scope = "email_register_request"

    assert target.allow_request(_fake_request({"email": "new@example.com"}), view=None)
