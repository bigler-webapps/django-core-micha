import hashlib
import hmac
import ipaddress
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import TestInstance, TestRun, TestMailMessage
from .isolation import IsolationFailure, validate_database_evidence, verify_marker


class TestManagementError(Exception):
    def __init__(self, code: str, detail: str, status: int = 403):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


@dataclass(frozen=True)
class Activation:
    instance_id: uuid.UUID
    token_digest: str


def _setting(name: str, default=None):
    return getattr(settings, name, default)


def validate_configuration() -> Activation:
    if _setting("TEST_MANAGEMENT_ENABLED", False) is not True:
        raise TestManagementError("test_management_disabled", "Test management is disabled.")
    if _setting("ENV_TYPE", "").lower() != "local":
        raise TestManagementError("test_management_environment_refused", "Test management is local-only.")
    if _setting("TEST_MANAGEMENT_ORIGINAL_ENV_TYPE", "").lower() != "local":
        raise TestManagementError("test_management_original_environment_refused", "The original environment is not disposable local development.")
    if _setting("TEST_MANAGEMENT_REMOTE_ACCESS", False) is not False:
        raise TestManagementError("test_management_remote_refused", "Remote test-management access is unsupported.")
    instance_value = _setting("TEST_MANAGEMENT_INSTANCE_ID", "")
    token = _setting("TEST_MANAGEMENT_CONTROLLER_TOKEN", "")
    if not instance_value or not token:
        raise TestManagementError("test_management_bootstrap_incomplete", "The isolated instance is not bootstrapped.")
    try:
        instance_id = uuid.UUID(str(instance_value))
    except (TypeError, ValueError) as exc:
        raise TestManagementError("test_management_instance_invalid", "The isolated instance identifier is invalid.") from exc
    try:
        proof_instance, database_ip = verify_marker()
        database = settings.DATABASES["default"]
        if (proof_instance != instance_id or not hmac.compare_digest(token, os.environ.get("TEST_MANAGEMENT_CONTROLLER_TOKEN", "")) or
                database.get("ENGINE") != "django.db.backends.postgresql" or database.get("USER") != "test_management" or
                str(database.get("PORT")) != "5432" or database.get("HOST") != "db" or database.get("NAME") != f"dcm_tm_{instance_id.hex}" or
                str(settings.MEDIA_ROOT) != "/app/backend/media" or settings.DEBUG is not False or
                settings.SESSION_ENGINE != "django.contrib.sessions.backends.db" or
                settings.CACHES["default"]["BACKEND"] != "django.core.cache.backends.locmem.LocMemCache" or
                settings.CHANNEL_LAYERS["default"]["BACKEND"] != "channels.layers.InMemoryChannelLayer" or
                settings.STORAGES["default"] != {"BACKEND": "django.core.files.storage.FileSystemStorage"} or
                settings.CORS_ALLOW_ALL_ORIGINS is not False or settings.CORS_ALLOWED_ORIGINS or
                settings.CORS_ALLOWED_ORIGIN_REGEXES or settings.CORS_ALLOW_CREDENTIALS is not False):
            raise IsolationFailure("The application isolation configuration is invalid.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), host(inet_server_addr()), current_setting('data_directory'), pg_read_file('/proc/self/mountinfo', 0, 1048576)")
            validate_database_evidence(instance_id, database_ip, cursor.fetchone())
    except Exception as exc:
        # A database exception may contain connection settings; never return it.
        raise TestManagementError("test_management_isolation_unverified", "Test-management isolation has not been verified.") from exc
    digest = _token_digest(token)
    return Activation(instance_id=instance_id, token_digest=digest)


def _token_digest(token: str) -> str:
    secret = str(_setting("SECRET_KEY", "test-management")).encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def ensure_instance() -> TestInstance:
    activation = validate_configuration()
    ttl = int(_setting("TEST_MANAGEMENT_CONTROLLER_TTL_SECONDS", 900))
    try:
        boot_id = uuid.UUID(str(_setting("TEST_MANAGEMENT_BOOT_ID", "")))
    except (TypeError, ValueError) as exc:
        raise TestManagementError("test_management_boot_invalid", "The instance boot identity is missing.", 403) from exc
    with transaction.atomic():
        instance, created = TestInstance.objects.get_or_create(
            instance_id=activation.instance_id,
            defaults={"controller_token_digest": activation.token_digest, "boot_id": boot_id,
                      "expires_at": timezone.now() + timedelta(seconds=ttl)},
        )
        instance = TestInstance.objects.select_for_update().get(pk=instance.pk)
        if not hmac.compare_digest(instance.controller_token_digest, activation.token_digest):
            raise TestManagementError("test_management_instance_mismatch", "The controller is bound to another instance.", 403)
        if instance.status != TestInstance.Status.ACTIVE or instance.expires_at <= timezone.now():
            raise TestManagementError("test_management_controller_expired", "The controller authorization has expired.", 401)
        if not created and instance.boot_id != boot_id:
            TestMailMessage.objects.filter(run__instance=instance).update(link="", status=TestMailMessage.Status.DISCARDED)
            TestRun.objects.filter(instance=instance, status__in=[TestRun.Status.ACTIVE, TestRun.Status.RESET]).update(
                status=TestRun.Status.RECOVERABLE, recovery_reason="restart")
            instance.boot_id = boot_id
            instance.save(update_fields=["boot_id"])
    return instance


def authorize_request(request) -> TestInstance:
    activation = validate_configuration()
    remote = request.META.get("REMOTE_ADDR", "")
    try:
        is_local = ipaddress.ip_address(remote).is_loopback
    except ValueError:
        is_local = False
    if not is_local:
        raise TestManagementError("test_management_remote_refused", "Test management accepts local controller calls only.", 403)
    if request.META.get("HTTP_ORIGIN") or request.META.get("HTTP_REFERER"):
        raise TestManagementError("test_management_browser_refused", "Browser-originated controller requests are not supported.", 403)
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer ") or not header[7:].strip():
        raise TestManagementError("controller_authorization_required", "A controller bearer credential is required.", 401)
    supplied = _token_digest(header[7:].strip())
    if not hmac.compare_digest(activation.token_digest, supplied):
        raise TestManagementError("controller_authorization_invalid", "The controller credential is invalid.", 401)
    return ensure_instance()
