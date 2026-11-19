import hashlib
import hmac
import json
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string

from .activation import TestManagementError, ensure_instance
from .models import TestAccount, TestFixture, TestInstance, TestRun, TestMailMessage


def adapter():
    path = getattr(settings, "TEST_MANAGEMENT_ADAPTER", "")
    if not path:
        raise TestManagementError("test_management_adapter_missing", "No application adapter is configured.", 500)
    value = import_string(path)
    return value() if isinstance(value, type) else value


def discovery() -> dict[str, Any]:
    instance = ensure_instance()
    result = adapter().discovery()
    return {"instance_id": str(instance.instance_id), **result}


def _run(instance, handle: str, *, allow_recoverable: bool = False, lock: bool = False) -> TestRun:
    try:
        query = TestRun.objects.select_for_update() if lock else TestRun.objects
        run = query.get(instance=instance, handle=handle)
    except (TestRun.DoesNotExist, ValueError):
        raise TestManagementError("run_not_found", "The run handle is unknown to this instance.", 404)
    if run.status == TestRun.Status.CLOSED:
        raise TestManagementError("run_closed", "The run is closed.", 409)
    if run.status == TestRun.Status.RECOVERABLE and not allow_recoverable:
        raise TestManagementError("run_recovery_required", "The run requires recovery before use.", 409)
    return run


def create_run(payload: dict[str, Any]) -> TestRun:
    instance = ensure_instance()
    with transaction.atomic():
        # Lock the parent even when no run exists, so concurrent first creates
        # cannot both pass the empty active-run query.
        TestInstance.objects.select_for_update().get(pk=instance.pk)
        active = TestRun.objects.select_for_update().filter(
            instance=instance,
            status__in=[TestRun.Status.ACTIVE, TestRun.Status.RECOVERABLE, TestRun.Status.RESET],
        ).first()
        existing = TestRun.objects.filter(instance=instance, idempotency_key=payload["idempotency_key"]).first()
        if existing:
            if existing.status == TestRun.Status.CLOSED:
                raise TestManagementError("run_closed", "The original run is closed.", 409)
            if existing.baseline != payload.get("baseline", {}):
                raise TestManagementError("idempotency_conflict", "The idempotency key was used with different data.", 409)
            return existing
        if active:
            raise TestManagementError("active_run_conflict", "Another run already owns this instance.", 409)
        run = TestRun.objects.create(instance=instance, baseline=payload.get("baseline", {}), idempotency_key=payload["idempotency_key"])
        run._created_now = True
        try:
            starter = getattr(adapter(), "start_run", None)
            if starter:
                run.adapter_state = starter(run=run, baseline=run.baseline) or {}
                run.save(update_fields=["adapter_state", "updated_at"])
        except TestManagementError:
            raise
        except Exception as exc:
            run.delete()
            raise TestManagementError("adapter_error", "The application adapter could not start the run.", 422) from exc
        return run


def account_payload(account: TestAccount) -> dict[str, Any]:
    effective = adapter().inspect_account(run=account.run, account=account)
    return {
        "handle": str(account.handle),
        "template": account.template,
        "requested": {"identity": account.synthetic_identity, "configuration": account.requested_configuration},
        "effective": effective,
        "status": account.status,
    }


def _creation_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


def create_account(instance, run_handle: str, payload: dict[str, Any]) -> TestAccount:
    with transaction.atomic():
        run = _run(instance, run_handle, lock=True)
        fingerprint = _creation_fingerprint(payload)
        existing = TestAccount.objects.filter(run=run, idempotency_key=payload["idempotency_key"]).first()
        if existing:
            if existing.status != TestAccount.Status.ACTIVE:
                raise TestManagementError("idempotency_conflict", "The original account is no longer active.", 409)
            if not hmac.compare_digest(existing.creation_fingerprint, fingerprint):
                raise TestManagementError("idempotency_conflict", "The idempotency key was used with different data.", 409)
            return existing
        account = TestAccount.objects.create(
            run=run,
            idempotency_key=payload["idempotency_key"],
            template=payload["template"],
            creation_fingerprint=fingerprint,
            synthetic_identity=payload["synthetic_identity"],
            requested_configuration=payload.get("configuration", {}),
            adapter_ref="pending",
        )
        try:
            result = adapter().create_account(run=run, account_handle=str(account.handle), payload=payload)
            account.adapter_ref = result.adapter_ref
            account.user_ref = result.user_ref
            account.effective_state = {**(result.effective or {}), "relationships": result.relationships or {}}
            account.save(update_fields=["adapter_ref", "user_ref", "effective_state", "updated_at"])
        except TestManagementError:
            account.delete()
            raise
        except Exception as exc:
            account.delete()
            raise TestManagementError("adapter_error", "The application adapter rejected account creation.", 422) from exc
        return account


def get_account(instance, run_handle: str, account_handle: str, *, lock: bool = False) -> TestAccount:
    run = _run(instance, run_handle, lock=lock)
    try:
        return TestAccount.objects.get(run=run, handle=account_handle, status=TestAccount.Status.ACTIVE)
    except (TestAccount.DoesNotExist, ValueError):
        raise TestManagementError("account_not_found", "The account handle is not owned by this run.", 404)


def update_account(instance, run_handle: str, account_handle: str, payload: dict[str, Any]) -> TestAccount:
    with transaction.atomic():
        account = get_account(instance, run_handle, account_handle, lock=True)
        run = account.run
        try:
            state = adapter().update_account(run=run, account=account, payload=payload)
        except TestManagementError:
            raise
        except Exception as exc:
            raise TestManagementError("adapter_error", "The application adapter rejected the account update.", 422) from exc
        if "configuration" in payload:
            account.requested_configuration = payload["configuration"]
        if "template" in payload:
            account.template = payload["template"]
        account.effective_state = state
        account.save(update_fields=["template", "requested_configuration", "effective_state", "updated_at"])
    return account


def delete_account(instance, run_handle: str, account_handle: str) -> None:
    failure = None
    with transaction.atomic():
        run = _run(instance, run_handle, lock=True)
        try:
            account = TestAccount.objects.get(run=run, handle=account_handle, status__in=[TestAccount.Status.ACTIVE, TestAccount.Status.CLEANUP_FAILED])
        except (TestAccount.DoesNotExist, ValueError):
            raise TestManagementError("account_not_found", "The account handle is not owned by this run.", 404)
        TestMailMessage.objects.filter(account=account).update(link="", status=TestMailMessage.Status.DISCARDED)
        try:
            with transaction.atomic():
                adapter().delete_account(run=run, account=account)
        except Exception as exc:
            failure = exc
        account.status = TestAccount.Status.CLEANUP_FAILED if failure else TestAccount.Status.DELETED
        account.save(update_fields=["status", "updated_at"])
    if failure:
        raise TestManagementError("cleanup_failed", "Account cleanup failed and is recoverable.", 500) from failure


def prepare_fixture(instance, run_handle: str, payload: dict[str, Any]) -> TestFixture:
    with transaction.atomic():
        run = _run(instance, run_handle, lock=True)
        existing = TestFixture.objects.filter(run=run, idempotency_key=payload["idempotency_key"]).first()
        if existing:
            if existing.status != TestFixture.Status.ACTIVE:
                raise TestManagementError("idempotency_conflict", "The original fixture is no longer active.", 409)
            if existing.name != payload["name"] or existing.parameters != payload.get("parameters", {}):
                raise TestManagementError("idempotency_conflict", "The idempotency key was used with different data.", 409)
            return existing
        fixture = TestFixture.objects.create(
            run=run, idempotency_key=payload["idempotency_key"], name=payload["name"],
            parameters=payload.get("parameters", {}), adapter_ref="pending",
        )
        try:
            state = adapter().prepare_fixture(
                run=run, fixture_handle=str(fixture.handle), name=fixture.name, parameters=fixture.parameters,
            )
            fixture.adapter_ref = str(state.get("adapter_ref", fixture.handle))
            fixture.state = state
            fixture.save(update_fields=["adapter_ref", "state"])
        except Exception as exc:
            fixture.delete()
            if isinstance(exc, TestManagementError):
                raise
            raise TestManagementError("adapter_error", "The application adapter rejected the fixture.", 422) from exc
        return fixture


def _finish_run(instance, handle: str, operation: str) -> TestRun:
    if operation not in {"reset", "close"}:
        raise TestManagementError("invalid_operation", "The requested operation is not supported.", 400)
    failure = None
    # Persist the cleanup intent before touching files; a process crash cannot
    # make a partially completed cleanup look like a healthy active run.
    with transaction.atomic():
        run = _run(instance, handle, allow_recoverable=True, lock=True)
        TestMailMessage.objects.filter(run=run).update(link="", status=TestMailMessage.Status.DISCARDED)
        run.status = TestRun.Status.RECOVERABLE
        run.recovery_reason = "cleanup"
        run.save(update_fields=["status", "recovery_reason", "updated_at"])
    with transaction.atomic():
        run = _run(instance, handle, allow_recoverable=True, lock=True)
        try:
            with transaction.atomic():
                result = getattr(adapter(), f"{operation}_run")(run=run)
        except Exception as exc:
            failure = exc
        if failure:
            run.status = TestRun.Status.RECOVERABLE
            run.save(update_fields=["status", "updated_at"])
        else:
            run.adapter_state = {**(run.adapter_state or {}), "last_cleanup": result or {}}
            run.status = TestRun.Status.CLOSED if operation == "close" else TestRun.Status.RESET
            run.recovery_reason = ""
            run.save(update_fields=["adapter_state", "status", "recovery_reason", "updated_at"])
            TestAccount.objects.filter(run=run, status__in=[TestAccount.Status.ACTIVE, TestAccount.Status.CLEANUP_FAILED]).update(status=TestAccount.Status.DELETED)
            TestFixture.objects.filter(run=run, status__in=[TestFixture.Status.ACTIVE, TestFixture.Status.CLEANUP_FAILED]).update(status=TestFixture.Status.DELETED)
    if failure:
        raise TestManagementError("cleanup_failed", "Run cleanup failed and is recoverable.", 500) from failure
    return run


def claim_recovery_message(instance, run_handle, account_handle):
    def missing():
        return TestManagementError("recovery_message_not_found", "The recovery message is unavailable.", 404)

    with transaction.atomic():
        TestInstance.objects.select_for_update().get(pk=instance.pk)
        try:
            account = get_account(instance, run_handle, account_handle, lock=True)
        except TestManagementError:
            raise missing() from None
        if account.run.status != TestRun.Status.ACTIVE:
            raise missing()
        message = TestMailMessage.objects.select_for_update().filter(
            run=account.run, account=account, kind="password_reset", status=TestMailMessage.Status.CAPTURED,
        ).first()
        if not message or not message.link:
            raise missing()
        result = {"handle": str(message.handle), "link": message.link}
        message.link = ""
        message.status = TestMailMessage.Status.CLAIMED
        message.save(update_fields=["link", "status", "updated_at"])
        return result
