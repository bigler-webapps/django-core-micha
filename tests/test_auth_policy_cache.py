"""Tests for the auth-policy read cache (DCM-PERF-1).

Covers: single-query caching, save()/delete() invalidation, the TTL fallback
value, cross-model cache-key safety, and output parity with the uncached path.
"""
from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from django_core_micha.auth.policy import (
    AUTH_POLICY_CACHE_TTL_SECONDS,
    get_or_create_auth_policy,
    get_policy_state,
)
from tests.testapp.models import AuthPolicyStub, AuthPolicyStubAlt


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub")
def test_second_call_hits_cache_not_db():
    """Two consecutive calls issue only one DB query in total."""
    with CaptureQueriesContext(connection) as ctx:
        first = get_or_create_auth_policy()
    first_query_count = len(ctx.captured_queries)
    assert first_query_count >= 1

    with CaptureQueriesContext(connection) as ctx:
        second = get_or_create_auth_policy()

    assert len(ctx.captured_queries) == 0, (
        f"Expected 0 queries on cached read, got {len(ctx.captured_queries)}: "
        f"{[q['sql'] for q in ctx.captured_queries]}"
    )
    assert first.pk == second.pk == 1


@pytest.mark.django_db
@override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub")
def test_save_invalidates_cache_immediately():
    """A .save() on the policy row is visible on the very next call, not
    only after TTL expiry."""
    policy = get_or_create_auth_policy()
    assert policy.allow_self_signup_open is False

    policy.allow_self_signup_open = True
    policy.save()

    refreshed = get_or_create_auth_policy()
    assert refreshed.allow_self_signup_open is True


@pytest.mark.django_db
@override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub")
def test_bypassed_write_stays_stale_until_cache_cleared():
    """A `.update()` bypasses post_save, so the cache is NOT invalidated —
    this is the documented TTL-fallback bound, not the normal path. Confirms
    the failure mode the TTL exists to bound, deterministically (no sleep)."""
    get_or_create_auth_policy()

    AuthPolicyStub.objects.filter(pk=1).update(allow_self_signup_open=True)

    stale = get_or_create_auth_policy()
    assert stale.allow_self_signup_open is False, (
        "a .update() bypasses the ORM signal, so this read should still be "
        "the stale cached value, not the new DB state"
    )

    cache.clear()
    fresh = get_or_create_auth_policy()
    assert fresh.allow_self_signup_open is True


@pytest.mark.django_db
@override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub")
def test_concurrent_write_during_db_read_is_not_cached_stale(monkeypatch):
    """If a save() lands on the row between this call's DB read and its own
    cache write (simulating a race under concurrent request load), the
    epoch guard must skip caching the now-stale value instead of writing it
    over the invalidation that just happened."""
    get_or_create_auth_policy()  # seed the row + connect signals
    cache.clear()

    real_get_or_create = type(AuthPolicyStub.objects).get_or_create

    def racing_get_or_create(self, *args, **kwargs):
        obj, created = real_get_or_create(self, *args, **kwargs)
        # Simulate another request's save() completing while this call's DB
        # read was in flight, i.e. after the DB read but before the cache
        # write below in get_or_create_auth_policy().
        AuthPolicyStub.objects.filter(pk=obj.pk).update(allow_self_signup_open=True)
        from django_core_micha.auth.policy import _invalidate_auth_policy_cache
        _invalidate_auth_policy_cache(sender=AuthPolicyStub)
        return obj, created

    monkeypatch.setattr(type(AuthPolicyStub.objects), "get_or_create", racing_get_or_create)
    get_or_create_auth_policy()
    monkeypatch.undo()

    with CaptureQueriesContext(connection) as ctx:
        follow_up = get_or_create_auth_policy()

    assert len(ctx.captured_queries) >= 1, (
        "the racing write should have prevented the earlier call from "
        "caching a stale value, forcing this call to re-read the DB"
    )
    assert follow_up.allow_self_signup_open is True


@pytest.mark.django_db
@override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub")
def test_delete_invalidates_cache():
    """A .delete() on the cached row means the next call re-creates it via
    get_or_create rather than returning the stale cached instance."""
    policy = get_or_create_auth_policy()
    policy.delete()

    recreated = get_or_create_auth_policy()
    assert recreated.pk == 1
    assert recreated.allow_self_signup_open is False


def test_ttl_is_a_named_bounded_constant():
    """The TTL fallback value is explicit and bounded, so a future edit
    can't silently drop it."""
    assert isinstance(AUTH_POLICY_CACHE_TTL_SECONDS, int)
    assert 0 < AUTH_POLICY_CACHE_TTL_SECONDS <= 300


@pytest.mark.django_db
def test_cross_model_cache_keys_do_not_collide():
    """Two different resolved AUTH_POLICY_MODEL values never read each
    other's cached row."""
    with override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub"):
        policy_a = get_or_create_auth_policy()
        policy_a.allow_self_signup_qr = True
        policy_a.save()

    with override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStubAlt"):
        policy_b = get_or_create_auth_policy()
        assert isinstance(policy_b, AuthPolicyStubAlt)
        assert policy_b.allow_self_signup_qr is False

    with override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub"):
        policy_a_again = get_or_create_auth_policy()
        assert isinstance(policy_a_again, AuthPolicyStub)
        assert policy_a_again.allow_self_signup_qr is True


@pytest.mark.django_db
@override_settings(AUTH_POLICY_MODEL="testapp.AuthPolicyStub")
def test_policy_state_output_parity_with_uncached_path():
    """get_policy_state()'s output shape/values are unchanged whether the
    policy object came from the cache or a fresh DB read."""
    policy = get_or_create_auth_policy()
    policy.allow_self_signup_open = True
    policy.allow_self_signup_email_domain = True
    policy.allowed_email_domains = ["example.com", "Example.com", "other.org"]
    policy.required_auth_factor_count = 2
    policy.signup_qr_expiry_days = 30
    policy.save()

    cache.clear()
    uncached_state = get_policy_state()

    warm = get_or_create_auth_policy()
    cached_state = get_policy_state(warm)

    assert cached_state == uncached_state
    assert cached_state.allow_self_signup_open is True
    assert cached_state.allowed_email_domains == ["example.com", "other.org"]
    assert cached_state.required_auth_factor_count == 2
    assert cached_state.signup_qr_expiry_days == 30
