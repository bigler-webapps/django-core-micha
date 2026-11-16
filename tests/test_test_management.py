import uuid

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from django_core_micha.test_management.models import TestAccount, TestFixture, TestRun
from django_core_micha.test_management import activation
from django_core_micha.test_management.protocol import AdapterAccountResult


class FakeAdapter:
    def discovery(self):
        return {"templates": [{"name": "fake"}], "supported_account_fields": ["name"], "supported_capabilities": ["x.read"], "fixtures": ["fake"]}

    def start_run(self, *, run, baseline):
        return {"baseline": baseline}

    def create_account(self, *, run, account_handle, payload):
        if payload["template"] == "bad":
            raise ValueError("adapter rejected")
        return AdapterAccountResult("fake-account", "fake-user", {"grants": ["x.read"]}, {"owner": str(run.handle)})

    def inspect_account(self, *, run, account):
        return account.effective_state

    def update_account(self, *, run, account, payload):
        return {"grants": payload.get("configuration", {}).get("grants", [])}

    def delete_account(self, *, run, account):
        return None

    def prepare_fixture(self, *, run, fixture_handle, name, parameters):
        return {"adapter_ref": fixture_handle, "parameters": parameters}

    def reset_run(self, *, run):
        return {"removed": True}

    def close_run(self, *, run):
        return {"removed": True}

    def recover_run(self, *, run):
        return {"recovered": True}


TOKEN = "controller-token-for-tests"
INSTANCE = "8f7d1d14-4d4e-41f8-b5d0-f79d8c09d3a6"
ISOLATION = {
    "TEST_MANAGEMENT_ENABLED": True,
    "ENV_TYPE": "local",
    "TEST_MANAGEMENT_ORIGINAL_ENV_TYPE": "local",
    "TEST_MANAGEMENT_ISOLATED_INSTANCE": True,
    "TEST_MANAGEMENT_DISPOSABLE_STORAGE": True,
    "TEST_MANAGEMENT_ISOLATED_DATABASE": True,
    "TEST_MANAGEMENT_ISOLATED_MEDIA": True,
    "TEST_MANAGEMENT_ISOLATED_CACHE": True,
    "TEST_MANAGEMENT_ISOLATED_SESSIONS": True,
    "TEST_MANAGEMENT_ISOLATED_BACKGROUND": True,
    "TEST_MANAGEMENT_REMOTE_ACCESS": False,
    "TEST_MANAGEMENT_INSTANCE_ID": INSTANCE,
    "TEST_MANAGEMENT_CONTROLLER_TOKEN": TOKEN,
    "TEST_MANAGEMENT_BOOT_ID": "135de580-b855-42e6-a23e-391592d2573d",
    "TEST_MANAGEMENT_ADAPTER": "tests.test_test_management.FakeAdapter",
}


@pytest.fixture
def client(monkeypatch):
    # Lifecycle tests exercise a synthetic adapter independently of the
    # container proof, which is covered by test_test_management_isolation.py.
    monkeypatch.setattr(activation, "validate_configuration", lambda: activation.Activation(uuid.UUID(INSTANCE), activation._token_digest(TOKEN)))
    value = APIClient()
    value.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    return value


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_test_management", **ISOLATION)
def test_activation_and_controller_boundary(client):
    assert client.get("/api/test-management/discovery/").status_code == 200
    assert client.get("/api/test-management/discovery/", HTTP_ORIGIN="http://localhost:3000").data["code"] == "test_management_browser_refused"
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert client.get("/api/test-management/discovery/").data["code"] == "controller_authorization_invalid"


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_test_management", **ISOLATION)
def test_authenticated_controller_remains_usable_when_default_throttle_is_exhausted(client, monkeypatch):
    from rest_framework.views import APIView

    class RejectAll:
        def allow_request(self, request, view):
            return False

        def wait(self):
            return 60

    monkeypatch.setattr(APIView, "throttle_classes", [RejectAll])
    for _ in range(3):
        assert client.get("/api/test-management/discovery/").status_code == 200
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert client.get("/api/test-management/discovery/").status_code in (401, 403)


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_test_management", **ISOLATION)
def test_run_and_account_idempotency_ownership_and_cleanup(client):
    response = client.post("/api/test-management/runs/", {"idempotency_key": "run-1", "baseline": {}}, format="json")
    assert response.status_code == 201
    run = response.data["handle"]
    body = {"idempotency_key": "account-1", "template": "fake", "synthetic_identity": {"name": "Synthetic"}, "password": "long-password-123"}
    created = client.post(f"/api/test-management/runs/{run}/accounts/", body, format="json")
    assert created.status_code == 201
    assert "password" not in created.data
    assert client.post(f"/api/test-management/runs/{run}/accounts/", body, format="json").data["handle"] == created.data["handle"]
    conflict = {**body, "synthetic_identity": {"name": "Different"}}
    assert client.post(f"/api/test-management/runs/{run}/accounts/", conflict, format="json").data["code"] == "idempotency_conflict"
    account = created.data["handle"]
    assert client.get(f"/api/test-management/runs/{uuid.uuid4()}/accounts/{account}/").status_code == 404
    assert client.delete(f"/api/test-management/runs/{run}/accounts/{account}/").status_code == 204
    assert TestAccount.objects.filter(handle=account, status=TestAccount.Status.DELETED).exists()


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_test_management", **ISOLATION)
def test_adapter_error_leaves_no_account_and_run_cleanup_is_truthful(client):
    run = client.post("/api/test-management/runs/", {"idempotency_key": "run-2"}, format="json").data["handle"]
    bad = {"idempotency_key": "bad", "template": "bad", "synthetic_identity": {}, "password": "long-password-123"}
    assert client.post(f"/api/test-management/runs/{run}/accounts/", bad, format="json").status_code == 422
    assert not TestAccount.objects.exists()
    fixture = client.post(f"/api/test-management/runs/{run}/fixtures/", {"idempotency_key": "fixture-1", "name": "fake"}, format="json")
    assert fixture.status_code == 201
    closed = client.post(f"/api/test-management/runs/{run}/close/", {}, format="json")
    assert closed.status_code == 200
    assert TestRun.objects.get(handle=run).status == TestRun.Status.CLOSED
    assert TestFixture.objects.get(handle=fixture.data["handle"]).status == TestFixture.Status.DELETED


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="tests.urls_test_management",
    TEST_MANAGEMENT_ENABLED=False,
    **{key: value for key, value in ISOLATION.items() if key != "TEST_MANAGEMENT_ENABLED"},
)
def test_disabled_configuration_refuses_before_mutation():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    response = client.get("/api/test-management/discovery/")
    assert response.status_code == 403
    assert response.data["code"] == "test_management_disabled"
    assert not TestRun.objects.exists()


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_test_management", **ISOLATION)
def test_restart_recovery_is_explicit_and_cleanup_failure_cannot_be_bypassed(client):
    created = client.post("/api/test-management/runs/", {"idempotency_key": "recovery"}, format="json")
    handle = created.data["handle"]
    run = TestRun.objects.get(handle=handle)
    run.status, run.recovery_reason = TestRun.Status.RECOVERABLE, "restart"
    run.save(update_fields=["status", "recovery_reason"])
    assert client.post(f"/api/test-management/runs/{handle}/recover/", {}, format="json").status_code == 200
    run.refresh_from_db()
    assert run.status == TestRun.Status.ACTIVE
    run.status, run.recovery_reason = TestRun.Status.RECOVERABLE, "cleanup"
    run.save(update_fields=["status", "recovery_reason"])
    assert client.post(f"/api/test-management/runs/{handle}/recover/", {}, format="json").status_code == 409
    run.refresh_from_db()
    assert run.status == TestRun.Status.RECOVERABLE
