"""S213 — DRF AuthZ-Denial AuditLog tests."""
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, Throttled
from rest_framework.response import Response

from django_core_micha.auditlog.models import AuditEvent
from django_core_micha.auth.exception_handler import custom_exception_handler

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
# NotAuthenticated → drf.not_authenticated
# ---------------------------------------------------------------------------

class TestNotAuthenticated:
    def test_creates_audit_event(self, db, rf):
        exc = NotAuthenticated()
        context = _make_context(rf)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.filter(event_type="drf.not_authenticated").first()
        assert event is not None

    def test_actor_is_none_for_anonymous(self, db, rf):
        exc = NotAuthenticated()
        context = _make_context(rf)
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.get(event_type="drf.not_authenticated")
        assert event.actor_id is None

    def test_metadata_contains_view_and_path(self, db, rf):
        exc = NotAuthenticated()
        context = _make_context(rf, path="/api/secure/", view_name="SecureView")
        custom_exception_handler(exc, context)
        event = AuditEvent.objects.get(event_type="drf.not_authenticated")
        assert event.metadata["view"] == "SecureView"
        assert event.metadata["path"] == "/api/secure/"
        assert event.metadata["method"] == "GET"

    def test_response_is_still_correct(self, db, rf):
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
            event_type__in=["drf.not_authenticated", "drf.permission_denied", "drf.throttled"]
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
