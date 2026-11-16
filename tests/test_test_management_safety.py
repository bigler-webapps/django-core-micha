from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.test import RequestFactory, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from django_core_micha.test_management import activation, services
from django_core_micha.test_management.models import TestInstance, TestRun
from django_core_micha.test_management.serializers import AccountCreateSerializer
from django_core_micha.test_management.protocol import AdapterAccountResult


@pytest.mark.django_db
@override_settings(TEST_MANAGEMENT_ENABLED=True, ENV_TYPE="local", TEST_MANAGEMENT_ORIGINAL_ENV_TYPE="local",
                   TEST_MANAGEMENT_INSTANCE_ID="8f7d1d14-4d4e-41f8-b5d0-f79d8c09d3a6",
                   TEST_MANAGEMENT_CONTROLLER_TOKEN="synthetic-controller-token-long-enough")
def test_unverified_isolation_refuses_without_creating_instance():
    with pytest.raises(activation.TestManagementError, match="isolation has not been verified"):
        activation.authorize_request(RequestFactory().get("/", HTTP_AUTHORIZATION="Bearer synthetic"))
    assert not TestInstance.objects.exists()


@pytest.mark.django_db
def test_invalid_controller_does_not_create_instance():
    proof = activation.Activation(uuid4(), activation._token_digest("expected-synthetic"))
    request = RequestFactory().get("/", HTTP_AUTHORIZATION="Bearer wrong-synthetic")
    with patch.object(activation, "validate_configuration", return_value=proof):
        with pytest.raises(activation.TestManagementError, match="credential is invalid"):
            activation.authorize_request(request)
    assert not TestInstance.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("extra", [{"REMOTE_ADDR": "203.0.113.10"}, {"HTTP_ORIGIN": "http://untrusted.example"},
                                    {"HTTP_REFERER": "http://untrusted.example/"}])
def test_remote_or_browser_controller_request_refuses_before_mutation(extra):
    proof = activation.Activation(uuid4(), activation._token_digest("expected-synthetic"))
    request = RequestFactory().get("/", HTTP_AUTHORIZATION="Bearer expected-synthetic", **extra)
    with patch.object(activation, "validate_configuration", return_value=proof):
        with pytest.raises(activation.TestManagementError):
            activation.authorize_request(request)
    assert not TestInstance.objects.exists()


@pytest.mark.django_db
def test_reset_run_still_blocks_competing_run():
    instance = TestInstance.objects.create(controller_token_digest="x", expires_at=timezone.now() + timedelta(minutes=1))
    TestRun.objects.create(instance=instance, idempotency_key="first", status=TestRun.Status.RESET)
    with patch.object(services, "ensure_instance", return_value=instance):
        with pytest.raises(activation.TestManagementError, match="Another run"):
            services.create_run({"idempotency_key": "second", "baseline": {}})


def test_unknown_create_field_is_rejected():
    serializer = AccountCreateSerializer(data={
        "idempotency_key": "key", "template": "fake", "synthetic_identity": {},
        "password": "synthetic-password", "is_staff": True,
    })
    assert not serializer.is_valid()
    assert "is_staff" in serializer.errors


@pytest.mark.django_db
def test_recoverable_run_cannot_be_used_until_recovered():
    instance = TestInstance.objects.create(controller_token_digest="x", expires_at=timezone.now() + timedelta(minutes=1))
    run = TestRun.objects.create(instance=instance, idempotency_key="first", status=TestRun.Status.RECOVERABLE)
    with pytest.raises(activation.TestManagementError, match="requires recovery"):
        services._run(instance, str(run.handle))
    assert services._run(instance, str(run.handle), allow_recoverable=True) == run


@pytest.mark.django_db
def test_account_idempotency_covers_password_and_survives_updates():
    instance = TestInstance.objects.create(controller_token_digest="x", expires_at=timezone.now() + timedelta(minutes=1))
    run = TestRun.objects.create(instance=instance, idempotency_key="run")

    class Adapter:
        def create_account(self, **kwargs):
            return AdapterAccountResult("owned", "user", {"grants": []}, {})

        def update_account(self, **kwargs):
            return {"grants": ["new"]}

    payload = {"idempotency_key": "same", "template": "fake", "synthetic_identity": {},
               "configuration": {}, "password": "synthetic-password"}
    with patch.object(services, "adapter", return_value=Adapter()):
        account = services.create_account(instance, str(run.handle), payload)
        services.update_account(instance, str(run.handle), str(account.handle), {"configuration": {"changed": True}})
        assert services.create_account(instance, str(run.handle), payload).pk == account.pk
        with pytest.raises(activation.TestManagementError, match="different data"):
            services.create_account(instance, str(run.handle), {**payload, "password": "different-password"})
    account.refresh_from_db()
    assert "synthetic-password" not in account.creation_fingerprint


@pytest.mark.django_db
def test_failed_reset_is_recoverable_and_retry_is_truthful():
    instance = TestInstance.objects.create(controller_token_digest="x", expires_at=timezone.now() + timedelta(minutes=1))
    run = TestRun.objects.create(instance=instance, idempotency_key="run")

    class Adapter:
        fail = True

        def reset_run(self, **kwargs):
            if self.fail:
                raise ValueError("synthetic error")
            return {"removed": True}

    app = Adapter()
    with patch.object(services, "adapter", return_value=app):
        with pytest.raises(activation.TestManagementError, match="cleanup failed"):
            services._finish_run(instance, str(run.handle), "reset")
        run.refresh_from_db()
        assert run.status == TestRun.Status.RECOVERABLE
        assert run.recovery_reason == "cleanup"
        app.fail = False
        finished = services._finish_run(instance, str(run.handle), "reset")
        assert finished.status == TestRun.Status.RESET
        assert finished.recovery_reason == ""


@pytest.mark.django_db
def test_new_server_boot_requires_explicit_recovery_of_existing_run():
    proof = activation.Activation(uuid4(), "a" * 64)
    boot_one, boot_two = uuid4(), uuid4()
    with patch.object(activation, "validate_configuration", return_value=proof):
        with override_settings(TEST_MANAGEMENT_BOOT_ID=str(boot_one)):
            instance = activation.ensure_instance()
            run = TestRun.objects.create(instance=instance, idempotency_key="run")
            assert activation.ensure_instance().boot_id == boot_one
            run.refresh_from_db()
            assert run.status == TestRun.Status.ACTIVE
        with override_settings(TEST_MANAGEMENT_BOOT_ID=str(boot_two)):
            assert activation.ensure_instance().boot_id == boot_two
            run.refresh_from_db()
            assert run.status == TestRun.Status.RECOVERABLE
            assert run.recovery_reason == "restart"


@pytest.mark.django_db
@override_settings(TEST_MANAGEMENT_CONTROLLER_TTL_SECONDS=7200)
def test_local_resume_renews_only_the_matching_instance_controller():
    instance = TestInstance.objects.create(controller_token_digest="a" * 64, expires_at=timezone.now() - timedelta(seconds=1))
    proof = activation.Activation(instance.instance_id, "a" * 64)
    with patch("django_core_micha.test_management.management.commands.renew_test_controller.validate_configuration", return_value=proof):
        call_command("renew_test_controller")
    instance.refresh_from_db()
    assert instance.expires_at > timezone.now() + timedelta(minutes=100)
    previous_expiry = instance.expires_at
    wrong = activation.Activation(instance.instance_id, "b" * 64)
    with patch("django_core_micha.test_management.management.commands.renew_test_controller.validate_configuration", return_value=wrong):
        with pytest.raises(CommandError):
            call_command("renew_test_controller")
    instance.refresh_from_db()
    assert instance.expires_at == previous_expiry


@pytest.mark.django_db
def test_expired_controller_cannot_access_the_instance():
    instance = TestInstance.objects.create(controller_token_digest="a" * 64, expires_at=timezone.now() - timedelta(seconds=1))
    proof = activation.Activation(instance.instance_id, "a" * 64)
    with patch.object(activation, "validate_configuration", return_value=proof), override_settings(TEST_MANAGEMENT_BOOT_ID=str(uuid4())):
        with pytest.raises(activation.TestManagementError) as error:
            activation.ensure_instance()
    assert error.value.code == "test_management_controller_expired"
