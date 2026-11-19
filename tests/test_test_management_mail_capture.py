from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest
from django.db import close_old_connections, connection
from django.test import override_settings
from rest_framework.test import APIClient

from django_core_micha.invitations.emails import send_invite_or_reset_email
from django_core_micha.test_management import activation, services
from django_core_micha.test_management.http_relay import allowed_path
from django_core_micha.test_management.local_bridge import dispatch
from django_core_micha.test_management.mail_capture import capture_password_reset
from django_core_micha.test_management.models import (
    TestAccount as ManagedAccount,
    TestInstance as ManagedInstance,
    TestMailMessage as CapturedMailMessage,
    TestRun as ManagedRun,
)
from tests.test_test_management import ISOLATION, INSTANCE, TOKEN
from tests.test_test_management_bridge import FakeConnection

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_settings():
    with override_settings(ROOT_URLCONF="tests.urls_test_management", **ISOLATION):
        yield


@pytest.fixture
def setup_run(monkeypatch):
    monkeypatch.setattr(activation, "validate_configuration", lambda: activation.Activation(uuid.UUID(INSTANCE), activation._token_digest(TOKEN)))
    instance = activation.ensure_instance()
    run = ManagedRun.objects.create(instance=instance, idempotency_key="run")
    account = ManagedAccount.objects.create(run=run, idempotency_key="a", template="fake", creation_fingerprint="a", adapter_ref="a", user_ref="101")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    return instance, run, account, client


def path(run, account):
    return f"/api/test-management/runs/{run.handle}/accounts/{account.handle}/recovery-message/"


def test_capture_claim_once_and_wrong_owner(setup_run, caplog):
    instance, run, account, client = setup_run
    user = SimpleNamespace(pk=101, email="synthetic@example.test")
    link = "https://example.test/reset/synthetic-private"
    with patch("django_core_micha.invitations.emails.EmailMessage.send") as send, caplog.at_level("INFO"):
        send_invite_or_reset_email(user=user, url=link, is_new_user=False)
        send.assert_not_called()
    assert "Password reset message captured" in caplog.text
    link_absent_from_logs = link not in caplog.text
    assert link_absent_from_logs, "captured recovery link appeared in logs"
    wrong = client.post(path(run, SimpleNamespace(handle=uuid.uuid4())))
    assert (wrong.status_code, wrong.data["code"]) == (404, "recovery_message_not_found")
    response = client.post(path(run, account))
    claim_matches = response.status_code == 200 and response.data.get("link") == link
    assert claim_matches, "claim did not return the expected recovery link"
    message = CapturedMailMessage.objects.get()
    assert response.data["handle"] == str(message.handle)
    consumed_material_cleared = message.status == "claimed" and message.link == ""
    assert consumed_material_cleared, "claimed message retained private material"
    assert client.post(path(run, account)).data["code"] == "recovery_message_not_found"


def test_capture_is_restricted_and_newer_discards_previous(setup_run, monkeypatch):
    instance, run, account, client = setup_run
    assert not capture_password_reset(SimpleNamespace(pk=102), "other")
    foreign_instance = ManagedInstance.objects.create(controller_token_digest="other", expires_at=instance.expires_at)
    foreign_run = ManagedRun.objects.create(instance=foreign_instance, idempotency_key="foreign")
    ManagedAccount.objects.create(run=foreign_run, idempotency_key="foreign-account", template="fake",
                                  creation_fingerprint="foreign", adapter_ref="foreign", user_ref="303")
    assert not capture_password_reset(SimpleNamespace(pk=303), "other-run")
    assert capture_password_reset(SimpleNamespace(pk=101), "first")
    assert capture_password_reset(SimpleNamespace(pk=101), "second")
    saved_messages_match = list(CapturedMailMessage.objects.order_by("id").values_list("status", "link")) == [
        ("discarded", ""), ("captured", "second"),
    ]
    assert saved_messages_match, "new capture did not discard the prior message"
    run.status = ManagedRun.Status.RECOVERABLE
    run.save(update_fields=["status"])
    assert not capture_password_reset(SimpleNamespace(pk=101), "third")
    assert client.post(path(run, account)).data["code"] == "recovery_message_not_found"
    with override_settings(TEST_MANAGEMENT_ENABLED=False):
        assert not capture_password_reset(SimpleNamespace(pk=101), "disabled")
    monkeypatch.setattr(activation, "validate_configuration", lambda: (_ for _ in ()).throw(ValueError("private")))
    assert not capture_password_reset(SimpleNamespace(pk=101), "failed")
    with patch("django_core_micha.test_management.mail_capture.apps.is_installed", return_value=False):
        assert not capture_password_reset(SimpleNamespace(pk=101), "absent")


@pytest.mark.parametrize("operation", ["reset", "close", "delete"])
def test_cleanup_discards_links(setup_run, operation):
    instance, run, account, client = setup_run
    assert capture_password_reset(SimpleNamespace(pk=101), "private")
    if operation == "delete":
        response = client.delete(f"/api/test-management/runs/{run.handle}/accounts/{account.handle}/")
    else:
        response = client.post(f"/api/test-management/runs/{run.handle}/{operation}/", {}, format="json")
    assert response.status_code in (200, 204)
    message = CapturedMailMessage.objects.get()
    cleanup_cleared_material = message.link == "" and message.status == "discarded"
    assert cleanup_cleared_material, "cleanup retained private recovery material"
    response = client.post(path(run, account))
    assert response.status_code == 404 and response.data["code"] == "recovery_message_not_found"


def test_restart_discards_before_recovery(setup_run):
    instance, run, account, client = setup_run
    assert capture_password_reset(SimpleNamespace(pk=101), "private")
    instance.boot_id = uuid.uuid4()
    instance.save(update_fields=["boot_id"])
    activation.ensure_instance()
    message = CapturedMailMessage.objects.get()
    assert message.link == "" and message.status == "discarded"
    run.refresh_from_db()
    assert run.status == ManagedRun.Status.RECOVERABLE
    assert client.post(f"/api/test-management/runs/{run.handle}/recover/", {}, format="json").status_code == 200
    assert client.post(path(run, account)).status_code == 404


def test_invite_and_non_test_path_unchanged(setup_run, caplog):
    user = SimpleNamespace(pk=101, email="synthetic@example.test")
    with patch("django_core_micha.invitations.emails.EmailMessage.send") as invite_send, \
            patch("django_core_micha.invitations.emails.email_texts.render_invite_email", return_value=("subject", "body")), \
            patch("django_core_micha.test_management.mail_capture.capture_password_reset") as capture:
        send_invite_or_reset_email(user=user, url="invite-link", is_new_user=True)
        capture.assert_not_called()
    invite_send.assert_not_called()
    with patch("django_core_micha.invitations.emails.EmailMessage.send") as local_send, \
            override_settings(TEST_MANAGEMENT_ENABLED=False), \
            patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", "ordinary-link")), \
            caplog.at_level("INFO"):
        send_invite_or_reset_email(user=user, url="ordinary-link", is_new_user=False)
    assert "ordinary-link" in caplog.text
    local_send.assert_not_called()
    with patch("django_core_micha.invitations.emails.EmailMessage.send") as production_send, \
            override_settings(ENV_TYPE="production"), \
            patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", "body")):
        send_invite_or_reset_email(user=user, url="production-reset-link", is_new_user=False)
    production_send.assert_called_once()


def test_bridge_only_allows_post_and_browser_blocks(setup_run):
    _, run, account, _ = setup_run
    route = path(run, account)
    FakeConnection.calls.clear()
    assert dispatch({"method": "POST", "path": route}, "x" * 32, FakeConnection)["status"] == 302
    for method in ("GET", "PATCH", "DELETE"):
        assert dispatch({"method": method, "path": route}, "x" * 32, FakeConnection)["body"]["code"] == "invalid_route"
    assert not allowed_path(route)


def test_reset_request_response_and_capture_failure(setup_run):
    from django.contrib.auth import get_user_model
    from django_core_micha.invitations.mixins import InviteActionsMixin

    _, run, account, _ = setup_run
    user = get_user_model().objects.create_user(username="synthetic", email="synthetic@example.test", password="synthetic-pass-123")
    account.user_ref = str(user.pk)
    account.save(update_fields=["user_ref"])
    view = InviteActionsMixin()
    view._build_frontend_url = lambda *args, **kwargs: "https://app.example.test/reset/synthetic"
    request = SimpleNamespace(data={"email": user.email})
    with patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", "https://app.example.test/reset/synthetic")):
        captured = view.reset_request(request)
        with override_settings(TEST_MANAGEMENT_ENABLED=False):
            disabled = view.reset_request(request)
        with patch("django_core_micha.test_management.mail_capture.capture_password_reset", side_effect=ValueError("private")):
            failed = view.reset_request(request)
        unknown = view.reset_request(SimpleNamespace(data={"email": "unknown@example.test"}))
    assert all(response.status_code == 200 and response.data == captured.data for response in (disabled, failed, unknown))
    assert CapturedMailMessage.objects.count() == 1


def test_reset_request_preserves_per_email_throttle(setup_run, monkeypatch):
    from django.contrib.auth import get_user_model
    from django.core.cache import cache
    from django.test import override_settings
    from rest_framework.permissions import AllowAny
    from rest_framework.test import APIRequestFactory

    from django_core_micha.auth.throttles import CoreAnonRateThrottle, PerEmailScopedRateThrottle
    from django_core_micha.auth.views import BaseUserViewSet

    _, _, account, _ = setup_run
    user = get_user_model().objects.create_user(
        username="throttled-reset", email="throttled@example.test", password="synthetic-password",
    )
    account.user_ref = str(user.pk)
    account.save(update_fields=["user_ref"])
    cache.clear()
    rates = {"anon": "100/hour", "email_password_reset": "1/hour"}
    monkeypatch.setattr(BaseUserViewSet, "throttle_classes", [CoreAnonRateThrottle])
    monkeypatch.setattr(CoreAnonRateThrottle, "THROTTLE_RATES", rates)
    monkeypatch.setattr(PerEmailScopedRateThrottle, "THROTTLE_RATES", rates)
    monkeypatch.setattr(BaseUserViewSet, "_build_frontend_url", lambda *args, **kwargs: "https://app.example.test/reset/synthetic")
    factory = APIRequestFactory()

    def dispatch(ip):
        request = factory.post(
            "/users/reset-request/", {"email": user.email}, format="json",
            HTTP_HOST="app.example.test", HTTP_CF_CONNECTING_IP=ip,
        )
        return BaseUserViewSet.as_view({"post": "reset_request"}, permission_classes=[AllowAny])(request)

    with override_settings(ALLOWED_HOSTS=["app.example.test"]):
        with patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", "https://app.example.test/reset/synthetic")):
            accepted = dispatch("192.0.2.10")
            throttled = dispatch("192.0.2.11")
    assert accepted.status_code == 200 and accepted.data["code"] == "Auth.RESET_REQUEST_ACCEPTED"
    assert throttled.status_code == 429
    assert CapturedMailMessage.objects.count() == 1


def test_reset_request_preserves_per_ip_throttle(setup_run, monkeypatch):
    from django.core.cache import cache
    from django.test import override_settings
    from rest_framework.permissions import AllowAny
    from rest_framework.test import APIRequestFactory

    from django_core_micha.auth.throttles import CoreAnonRateThrottle, PerEmailScopedRateThrottle
    from django_core_micha.auth.views import BaseUserViewSet

    cache.clear()
    rates = {"anon": "1/hour", "email_password_reset": "100/hour"}
    monkeypatch.setattr(BaseUserViewSet, "throttle_classes", [CoreAnonRateThrottle])
    monkeypatch.setattr(CoreAnonRateThrottle, "THROTTLE_RATES", rates)
    monkeypatch.setattr(PerEmailScopedRateThrottle, "THROTTLE_RATES", rates)
    factory = APIRequestFactory()

    def dispatch(email):
        request = factory.post(
            "/users/reset-request/", {"email": email}, format="json",
            HTTP_HOST="app.example.test", HTTP_CF_CONNECTING_IP="192.0.2.20",
        )
        return BaseUserViewSet.as_view({"post": "reset_request"}, permission_classes=[AllowAny])(request)

    with override_settings(ALLOWED_HOSTS=["app.example.test"]):
        first = dispatch("first-unknown@example.test")
        second = dispatch("second-unknown@example.test")
    assert first.status_code == 200 and first.data["code"] == "Auth.RESET_REQUEST_ACCEPTED"
    assert second.status_code == 429


def test_different_owned_accounts_keep_separate_links(setup_run):
    instance, run, account, client = setup_run
    other = ManagedAccount.objects.create(run=run, idempotency_key="b", template="fake", creation_fingerprint="b", adapter_ref="b", user_ref="202")
    assert capture_password_reset(SimpleNamespace(pk=101), "first-link")
    assert capture_password_reset(SimpleNamespace(pk=202), "second-link")
    other_claim_matches = client.post(path(run, other)).data.get("link") == "second-link"
    assert other_claim_matches, "the other account received the wrong recovery link"
    first_claim_matches = client.post(path(run, account)).data.get("link") == "first-link"
    assert first_claim_matches, "the first account received the wrong recovery link"
    foreign_instance = ManagedInstance.objects.create(controller_token_digest="x", expires_at=instance.expires_at)
    foreign_run = ManagedRun.objects.create(instance=foreign_instance, idempotency_key="foreign")
    response = client.post(path(foreign_run, account))
    assert response.status_code == 404 and response.data["code"] == "recovery_message_not_found"


def test_capture_rejects_ambiguous_account_owner(setup_run):
    _, run, _, _ = setup_run
    ManagedAccount.objects.create(
        run=run, idempotency_key="duplicate-owner", template="fake",
        creation_fingerprint="duplicate-owner", adapter_ref="duplicate-owner", user_ref="101",
    )
    assert not capture_password_reset(SimpleNamespace(pk=101), "ambiguous-link")
    assert CapturedMailMessage.objects.count() == 0


@pytest.mark.skipif(
    not connection.features.has_select_for_update,
    reason="concurrent capture requires the row locks used by the isolated PostgreSQL runtime",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_captures_for_different_accounts_keep_links_separate(setup_run):
    _, run, account, _ = setup_run
    other = ManagedAccount.objects.create(run=run, idempotency_key="b", template="fake",
                                          creation_fingerprint="b", adapter_ref="b", user_ref="202")
    start = Barrier(2)

    def capture(user_id, link):
        close_old_connections()
        try:
            start.wait(timeout=10)
            return capture_password_reset(SimpleNamespace(pk=user_id), link)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(capture, 101, "first-concurrent-link")
        second = pool.submit(capture, 202, "second-concurrent-link")
        assert first.result(timeout=15)
        assert second.result(timeout=15)

    first_link_matches = CapturedMailMessage.objects.get(account=account).link == "first-concurrent-link"
    second_link_matches = CapturedMailMessage.objects.get(account=other).link == "second-concurrent-link"
    assert first_link_matches, "the first account received the wrong concurrent link"
    assert second_link_matches, "the second account received the wrong concurrent link"


@pytest.mark.skipif(
    not connection.features.has_select_for_update,
    reason="concurrent claim requires the row locks used by the isolated PostgreSQL runtime",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_consume_link_once(setup_run):
    _, run, account, _ = setup_run
    link = "concurrent-claim-link"
    assert capture_password_reset(SimpleNamespace(pk=101), link)
    start = Barrier(2)

    def claim():
        close_old_connections()
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
            start.wait(timeout=10)
            response = client.post(path(run, account))
            if response.status_code == 200:
                return response.status_code, response.data.get("link")
            return response.status_code, response.data.get("code")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim)
        second = pool.submit(claim)
        results = [first.result(timeout=15), second.result(timeout=15)]

    assert results.count((200, link)) == 1
    assert results.count((404, "recovery_message_not_found")) == 1


def test_isolation_failure_never_logs_reset_link(setup_run, monkeypatch, caplog):
    user = SimpleNamespace(pk=101, email="synthetic@example.test")
    link = "https://example.test/reset/private-on-failure"
    monkeypatch.setattr(activation, "validate_configuration", lambda: (_ for _ in ()).throw(ValueError("private")))
    with patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", link)):
        with caplog.at_level("INFO"):
            send_invite_or_reset_email(user=user, url=link, is_new_user=False)
    link_absent_from_logs = link not in caplog.text
    assert link_absent_from_logs, "recovery link appeared in logs after isolation failure"
    assert "test_management_mail_capture_failed" in caplog.text


def test_unrendered_reset_link_is_not_captured_or_logged(setup_run, caplog):
    user = SimpleNamespace(pk=101, email="synthetic@example.test")
    link = "https://example.test/reset/not-in-message"
    with patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", "message without link")):
        with caplog.at_level("INFO"):
            send_invite_or_reset_email(user=user, url=link, is_new_user=False)
    assert CapturedMailMessage.objects.count() == 0
    assert link not in caplog.text
    with patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", f"Open this link: {link}.")):
        send_invite_or_reset_email(user=user, url=link, is_new_user=False)
    stored_from_message = CapturedMailMessage.objects.get().link == link
    assert stored_from_message, "the link was not taken from the rendered message"


def test_local_reset_logging_is_unchanged_when_test_management_app_is_absent(setup_run, caplog):
    user = SimpleNamespace(pk=101, email="synthetic@example.test")
    link = "https://example.test/reset/local-without-management"
    with patch("django_core_micha.invitations.emails.apps.is_installed", return_value=False), \
            patch("django_core_micha.invitations.emails.email_texts.render_reset_email", return_value=("subject", link)), \
            caplog.at_level("INFO"):
        send_invite_or_reset_email(user=user, url=link, is_new_user=False)
    assert link in caplog.text


def test_claimed_link_completes_the_real_confirmation_flow_once(setup_run):
    """Real rendering and link generation: the claimed link resets only its own account, once."""
    import re

    from django.contrib.auth import get_user_model
    from django.core.cache import cache
    from rest_framework.test import APIRequestFactory

    from django_core_micha.invitations.mixins import InviteActionsMixin
    from django_core_micha.invitations.views import PasswordResetConfirmView

    _, run, account, client = setup_run
    cache.clear()
    users = get_user_model().objects
    user = users.create_user(username="real-flow", email="real-flow@example.test", password="old-synthetic-pass-1")
    other = users.create_user(username="real-other", email="real-other@example.test", password="other-synthetic-pass-1")
    account.user_ref = str(user.pk)
    account.save(update_fields=["user_ref"])

    view = InviteActionsMixin()
    request = SimpleNamespace(data={"email": user.email}, build_absolute_uri=lambda p: "https://app.example.test" + p)
    assert view.reset_request(request).data["code"] == "Auth.RESET_REQUEST_ACCEPTED"
    claimed = client.post(path(run, account))
    assert claimed.status_code == 200
    match = re.search(r"/reset/([^/]+)/([^/]+)/$", claimed.data["link"])
    assert match, "the claimed link is not a real reset link"
    uid, token = match.groups()

    from django_core_micha.auth.throttles import CoreAnonRateThrottle
    from rest_framework.throttling import ScopedRateThrottle

    rates = {"password_reset": "1000/hour", "anon": "1000/hour"}

    def confirm(password):
        post = APIRequestFactory().post("/", {"new_password": password}, format="json")
        # DRF reads these class attributes at import time, so patch them directly.
        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", rates), \
                patch.object(CoreAnonRateThrottle, "THROTTLE_RATES", rates):
            return PasswordResetConfirmView.as_view()(post, uidb64=uid, token=token)

    assert confirm("new-synthetic-pass-9!").status_code == 200
    user.refresh_from_db()
    other.refresh_from_db()
    assert user.check_password("new-synthetic-pass-9!") and not user.check_password("old-synthetic-pass-1")
    assert other.check_password("other-synthetic-pass-1")
    assert confirm("another-synthetic-pass-8!").status_code == 400  # consumed link
    assert client.post(path(run, account)).data["code"] == "recovery_message_not_found"
