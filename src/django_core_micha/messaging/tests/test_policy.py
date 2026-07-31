import pytest

from django_core_micha.messaging.policy import get_messaging_policy, register_messaging_policy, unregister_messaging_policy


class Policy:
    def can_open_direct(self, **kwargs): return True
    def can_view_conversation(self, **kwargs): return True
    def can_post(self, **kwargs): return True
    def moderation_rights(self, **kwargs): return frozenset()
    def resolve_recipients(self, **kwargs): return []
    def provision_membership(self, **kwargs): return {"members": [], "remove_absent": False}
    def validate_scope(self, **kwargs): return {}


def test_policy_registration_is_single_deterministic_provider():
    policy = Policy()
    register_messaging_policy("policy-test", policy)
    assert get_messaging_policy("policy-test") is policy
    with pytest.raises(ValueError):
        register_messaging_policy("policy-test", Policy())
    unregister_messaging_policy("policy-test")
    with pytest.raises(LookupError):
        get_messaging_policy("policy-test")
