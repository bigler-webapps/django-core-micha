import pytest
from django.test import override_settings

from django_core_micha.ai_policy.models import AIModelPolicy
from django_core_micha.ai_policy.policy import PolicyError, apply_policy, refresh_policy, validate_policy
from django_core_micha.ai_policy.resolver import resolve_workload

pytestmark = pytest.mark.django_db


def policy(revision=1, standard_source="acct-a", standard_model="same"):
    return {
        "app_id": "bigler-consult",
        "environment": "staging",
        "revision": revision,
        "allowed": {"acct-a": ["same", "other"], "acct-b": ["same"]},
        "standard": {"source_id": standard_source, "model_id": standard_model},
    }


@pytest.fixture
def managed_settings(monkeypatch):
    monkeypatch.setattr("django_core_micha.ai_policy.resolver.find_spec", lambda package: object() if package == "openai" else None)
    return override_settings(
        DCM_AI_POLICY_ENABLED=True,
        DCM_AI_POLICY_APP_ID="bigler-consult",
        DCM_AI_POLICY_ENVIRONMENT="staging",
        DCM_AI_POLICY_SOURCES={
            "acct-a": {"driver": "openai_api", "label": "Account A", "credential_setting": "ACCOUNT_A_KEY"},
            "acct-b": {"driver": "openai_api", "label": "Account B", "credential_setting": "ACCOUNT_B_KEY"},
        },
        ACCOUNT_A_KEY="a-secret",
        ACCOUNT_B_KEY="b-secret",
    )


def test_validate_rejects_bad_revision_and_identity():
    with pytest.raises(PolicyError) as error:
        validate_policy({**policy(), "revision": True})
    assert error.value.code == "invalid_policy"
    with pytest.raises(PolicyError) as error:
        validate_policy(policy(), expected_app_id="other-app")
    assert error.value.code == "policy_target_mismatch"
    with pytest.raises(PolicyError, match="policy_target_mismatch"):
        validate_policy(policy(), expected_environment="production")


def test_apply_is_revisioned_and_same_revision_conflicts(managed_settings):
    with managed_settings:
        assert apply_policy(policy()) is True
        assert apply_policy(policy()) is False
        assert AIModelPolicy.objects.get().revision == 1
        with pytest.raises(PolicyError) as error:
            apply_policy({**policy(), "allowed": {"acct-a": ["same", "different"], "acct-b": ["same"]}})
        assert error.value.code == "conflicting_revision"
        assert apply_policy(policy(2, "acct-b")) is True
        state = AIModelPolicy.objects.get()
        assert state.revision == 2 and state.standard_source_id == "acct-b"


def test_resolver_keeps_source_identity_and_never_uses_legacy_credential(managed_settings):
    rows = [
        {"source_id": "acct-a", "driver": "openai_api", "model": "same", "efforts": ["low"]},
        {"source_id": "acct-b", "driver": "openai_api", "model": "same", "efforts": ["low"]},
    ]
    with managed_settings:
        apply_policy(policy())
        standard = resolve_workload(None, rows=rows, effort="low")
        explicit = resolve_workload(None, rows=rows, source="acct-b", model="same", effort="low")
        assert standard.source_id == "acct-a" and standard.credential == "a-secret"
        assert "a-secret" not in repr(standard)
        assert explicit.source_id == "acct-b" and explicit.credential == "b-secret"
        with override_settings(ACCOUNT_B_KEY=""):
            with pytest.raises(ValueError) as error:
                resolve_workload(None, rows=rows, source="acct-b", model="same", effort="low")
        assert str(error.value) == "invalid_selection"


def test_refresh_acknowledges_durable_revision_and_ignores_delayed_update(managed_settings):
    class Transport:
        payload = policy(2, "acct-b")
        reads = 0

        def get(self):
            self.reads += 1
            return self.payload

        def applied(self, revision):
            assert revision == AIModelPolicy.objects.get().revision == 2

    transport = Transport()
    with managed_settings:
        assert refresh_policy(transport=transport).revision == 2
        assert refresh_policy(transport=transport).revision == 2
        assert transport.reads == 1
        transport.payload = policy(1)
        assert refresh_policy(transport=transport, force=True).revision == 2


def test_managed_resolver_keeps_last_applied_policy_during_outage(monkeypatch, managed_settings):
    def outage():
        raise RuntimeError("offline")

    monkeypatch.setattr("django_core_micha.ai_policy.resolver.refresh_policy", outage)
    rows = [{"source_id": "acct-a", "driver": "openai_api", "model": "same", "efforts": ["low"]}]
    with managed_settings:
        with pytest.raises(ValueError, match="unavailable"):
            resolve_workload(None, rows=rows)
        apply_policy(policy())
        assert resolve_workload(None, rows=rows).source_id == "acct-a"
