import pytest

from django_core_micha.activity.policy import (
    get_activity_policy,
    register_activity_policy,
    unregister_activity_policy,
)


class Policy:
    def can_read_activity(self, **kwargs):
        return True


def test_policy_registration_is_single_deterministic_provider():
    policy = Policy()
    register_activity_policy("policy-test", policy)
    assert get_activity_policy("policy-test") is policy
    with pytest.raises(ValueError):
        register_activity_policy("policy-test", Policy())
    unregister_activity_policy("policy-test")
    with pytest.raises(LookupError):
        get_activity_policy("policy-test")


def test_unregistered_app_key_fails_closed():
    with pytest.raises(LookupError):
        get_activity_policy("never-registered")
