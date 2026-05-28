"""S213 — DRF AuthZ-Denial AuditLog tests."""
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, Throttled
from rest_framework.response import Response

from django_core_micha.auditlog.models import AuditEvent
from django_core_micha.auth.exception_handler import custom_exception_handler

# NotAuthenticated (401) is intentionally NOT logged (S3: unauthenticated DB amplification risk).

User = get_user_model()


@pytest.fixture()
def user(db):
    return User.objects.create_user(username="tester", password="pw", email="t@t.com")


@pytest.fixture()
def rf():
    return RequestFactory()


def _make_context(rf, user=None, path="/api/test/", view_name="TestView", action=None):
    req = rf.get(path)
    if user is not None:
        req.user = user
    else:
        from django.contrib.auth.models import AnonymousUser
        req.user = AnonymousUser()
    view = MagicMock()
    view.__class__.__name__ = view_name
    view.action = action
    return {"request": req, "view": view}


# ---------------------------------------------------------------------------
# NotAuthenticated (401) — NOT logged (S3: unauthenticated DB amplification risk)
# ---------------------------------------------------------------------------

class TestNotAuthenticated:
    def test_no_audit_event_created(self, db, rf):
        exc = NotAuthenticated()
        context = _make_context(rf)
        custom_exception_handler(exc, context)
        assert AuditEvent.objects.filter(event_type="drf.not_authenticated").count() == 0

    def test_response_is_still_401(self, db, rf):
        exc = NotAuthenticated()
        context = _make_context(rf)
        resp = custom_exception_handler(exc, context)
        assert resp is not None
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PermissionDenied → drf.permission_denied
# ---------------------------------------------------------------------------

class TestPermissionDenied:
    def test_creates_audit_event(self, user, db, rf):
        exc = PermissionDenied()
        context = _make_context(rf, user=user)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.filter(event_type="drf.permission_denied").first()
        assert event is not None

    def test_actor_is_authenticated_user(self, user, db, rf):
        exc = PermissionDenied()
        context = _make_context(rf, user=user)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.get(event_type="drf.permission_denied")
        assert event.actor_id == user.pk

    def test_viewset_action_captured(self, user, db, rf):
        exc = PermissionDenied()
        context = _make_context(rf, user=user, view_name="ItemViewSet", action="destroy")
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.get(event_type="drf.permission_denied")
        assert event.metadata["action"] == "destroy"
        assert event.metadata["view"] == "ItemViewSet"

    def test_error_code_not_detail_string(self, user, db, rf):
        exc = PermissionDenied("User foo@example.com lacks permission X")
        context = _make_context(rf, user=user)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.get(event_type="drf.permission_denied")
        # Full detail string must not be stored — only the error code (S4)
        assert "foo@example.com" not in str(event.metadata)
        assert "error_code" in event.metadata

    def test_response_is_still_403(self, user, db, rf):
        exc = PermissionDenied()
        context = _make_context(rf, user=user)
        resp = custom_exception_handler(exc, context)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Throttled → drf.throttled
# ---------------------------------------------------------------------------

class TestThrottled:
    def test_creates_audit_event(self, user, db, rf):
        exc = Throttled(wait=30)
        context = _make_context(rf, user=user)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.filter(event_type="drf.throttled").first()
        assert event is not None

    def test_retry_after_in_metadata(self, user, db, rf):
        exc = Throttled(wait=30)
        context = _make_context(rf, user=user)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.get(event_type="drf.throttled")
        assert event.metadata["retry_after"] == 30

    def test_response_is_still_429(self, user, db, rf):
        exc = Throttled(wait=5)
        context = _make_context(rf, user=user)
        resp = custom_exception_handler(exc, context)
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Unhandled exception — original handler return value (None) preserved
# ---------------------------------------------------------------------------

class TestUnhandledException:
    def test_none_returned_for_unhandled(self, db, rf):
        exc = ValueError("unexpected")
        context = _make_context(rf)
        resp = custom_exception_handler(exc, context)
        assert resp is None

    def test_no_audit_event_for_unhandled(self, db, rf):
        exc = ValueError("unexpected")
        context = _make_context(rf)
        custom_exception_handler(exc, context)
        assert AuditEvent.objects.filter(
            event_type__in=["drf.permission_denied", "drf.throttled"]
        ).count() == 0


# ---------------------------------------------------------------------------
# AuditEvent write failure must not abort the response
# ---------------------------------------------------------------------------

class TestAuditFailureSafety:
    def test_response_returned_even_when_audit_raises(self, db, rf):
        exc = NotAuthenticated()
        context = _make_context(rf)
        with patch("django_core_micha.auditlog.models.AuditEvent") as mock_ae:
            mock_ae.objects.create.side_effect = Exception("DB down")
            resp = custom_exception_handler(exc, context)
        assert resp is not None
        assert resp.status_code == 401
