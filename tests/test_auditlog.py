"""Tests for django_core_micha.auditlog."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.utils import timezone

from tests.testapp.models import Widget

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registry():
    from django.db.models.signals import post_delete, post_save, pre_save

    from django_core_micha.auditlog import signals as sig_mod
    from django_core_micha.auditlog.registry import _REGISTRY

    snapshot = dict(_REGISTRY)
    sig_mod._SIGNALS_CONNECTED = False
    yield
    # Disconnect any signals wired during the test (R4).
    for label in _REGISTRY:
        uid = f"auditlog_{label}"
        pre_save.disconnect(dispatch_uid=f"{uid}_pre_save")
        post_save.disconnect(dispatch_uid=f"{uid}_post_save")
        post_delete.disconnect(dispatch_uid=f"{uid}_post_delete")
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)
    sig_mod._SIGNALS_CONNECTED = False


@pytest.fixture()
def user(db):
    return User.objects.create_user(username="tester", password="pw", email="t@t.com")


# ---------------------------------------------------------------------------
# registry.py
# ---------------------------------------------------------------------------

class TestRegister:
    def test_registers_model_class(self):
        from django_core_micha.auditlog import register
        from django_core_micha.auditlog.registry import get_registry
        register(Widget, redact_fields=frozenset({"secret"}))
        assert "testapp.widget" in get_registry()

    def test_registers_string_label(self):
        from django_core_micha.auditlog import register
        from django_core_micha.auditlog.registry import get_registry
        register("testapp.Widget", redact_fields=frozenset())
        assert "testapp.widget" in get_registry()

    def test_duplicate_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from django_core_micha.auditlog import register
        register(Widget)
        with pytest.raises(ImproperlyConfigured, match="already registered"):
            register(Widget)

    def test_redact_fields_stored_as_frozenset(self):
        from django_core_micha.auditlog import register
        from django_core_micha.auditlog.registry import get_registry
        register(Widget, redact_fields={"secret"})
        entry = get_registry()["testapp.widget"]
        assert isinstance(entry.redact_fields, frozenset)
        assert "secret" in entry.redact_fields

    def test_context_resolver_stored(self):
        from django_core_micha.auditlog import register
        from django_core_micha.auditlog.registry import get_registry
        resolver = lambda inst: "ctx"
        register(Widget, context_resolver=resolver)
        assert get_registry()["testapp.widget"].context_resolver is resolver


# ---------------------------------------------------------------------------
# signals.py — field diff + audit event creation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSignals:
    def _setup(self, redact_fields=frozenset(), context_resolver=None):
        from django_core_micha.auditlog import register
        from django_core_micha.auditlog.signals import connect_signals
        register(Widget, redact_fields=redact_fields, context_resolver=context_resolver)
        connect_signals()

    def test_created_event(self):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup()
        Widget.objects.create(name="w1")
        ev = AuditEvent.objects.get(event_type="testapp.widget.created")
        assert ev.metadata["action"] == "created"
        assert ev.metadata["changes"]["name"]["from"] is None
        assert ev.metadata["changes"]["name"]["to"] == "w1"

    def test_updated_event_only_on_change(self):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup()
        w = Widget.objects.create(name="w1")
        AuditEvent.objects.all().delete()

        w.name = "w2"
        w.save()
        evs = AuditEvent.objects.filter(event_type="testapp.widget.updated")
        assert evs.count() == 1
        assert evs.first().metadata["changes"]["name"] == {"from": "w1", "to": "w2"}

    def test_no_event_on_unchanged_save(self):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup()
        w = Widget.objects.create(name="w1")
        AuditEvent.objects.all().delete()
        w.save()
        assert not AuditEvent.objects.filter(event_type="testapp.widget.updated").exists()

    def test_deleted_event(self):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup()
        w = Widget.objects.create(name="del-me")
        AuditEvent.objects.all().delete()
        w.delete()
        ev = AuditEvent.objects.get(event_type="testapp.widget.deleted")
        assert ev.metadata["action"] == "deleted"
        assert ev.metadata["changes"]["name"]["to"] is None

    def test_pii_redaction_on_create(self):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup(redact_fields=frozenset({"secret"}))
        Widget.objects.create(name="w1", secret="bank-iban-123")
        ev = AuditEvent.objects.get(event_type="testapp.widget.created")
        assert ev.metadata["changes"]["secret"]["to"] == "***"

    def test_pii_redaction_on_update(self):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup(redact_fields=frozenset({"secret"}))
        w = Widget.objects.create(name="w1", secret="old")
        AuditEvent.objects.all().delete()
        w.secret = "new-iban"
        w.save()
        ev = AuditEvent.objects.get(event_type="testapp.widget.updated")
        change = ev.metadata["changes"]["secret"]
        assert change["from"] == "***"
        assert change["to"] == "***"

    def test_actor_from_context_var(self, user):
        from django_core_micha.auditlog.audit_context import (
            reset_current_actor,
            set_current_actor,
        )
        from django_core_micha.auditlog.models import AuditEvent
        self._setup()
        token = set_current_actor(user)
        try:
            Widget.objects.create(name="actor-test")
        finally:
            reset_current_actor(token)
        ev = AuditEvent.objects.get(event_type="testapp.widget.created")
        assert ev.actor_id == user.pk

    def test_actor_fallback_to_updated_by(self, user):
        from django_core_micha.auditlog.models import AuditEvent
        self._setup()
        w = Widget.objects.create(name="w1")
        AuditEvent.objects.all().delete()
        w.name = "w2"
        w.updated_by = user
        w.save()
        ev = AuditEvent.objects.get(event_type="testapp.widget.updated")
        assert ev.actor_id == user.pk

    def test_context_resolver_called(self):
        from django_core_micha.auditlog.models import AuditEvent
        resolver = lambda inst: f"widget-{inst.pk}"
        self._setup(context_resolver=resolver)
        w = Widget.objects.create(name="ctx-test")
        ev = AuditEvent.objects.get(event_type="testapp.widget.created")
        assert ev.metadata["context"] == f"widget-{w.pk}"


# ---------------------------------------------------------------------------
# services/redact.py
# ---------------------------------------------------------------------------

class TestRedact:
    def test_redact_snapshot(self):
        from django_core_micha.auditlog.services.redact import redact_snapshot
        snap = {"bank_iban": "DE89...", "name": "Alice"}
        redact_snapshot(snap, frozenset({"bank_iban"}))
        assert snap["bank_iban"] == "***"
        assert snap["name"] == "Alice"

    def test_redact_snapshot_skips_already_redacted(self):
        from django_core_micha.auditlog.services.redact import redact_snapshot
        snap = {"bank_iban": "***"}
        changed = redact_snapshot(snap, frozenset({"bank_iban"}))
        assert not changed

    def test_redact_changes(self):
        from django_core_micha.auditlog.services.redact import redact_changes
        changes = {"bank_iban": {"from": "DE89", "to": "CH56"}}
        redact_changes(changes, frozenset({"bank_iban"}))
        assert changes["bank_iban"]["from"] == "***"
        assert changes["bank_iban"]["to"] == "***"

    def test_redact_metadata_full(self):
        from django_core_micha.auditlog.services.redact import redact_metadata
        metadata = {
            "before": {"bank_iban": "DE89", "name": "Alice"},
            "after": {"bank_iban": "CH56", "name": "Alice"},
            "changes": {"bank_iban": {"from": "DE89", "to": "CH56"}},
        }
        redact_metadata(metadata, frozenset({"bank_iban"}))
        assert metadata["before"]["bank_iban"] == "***"
        assert metadata["after"]["bank_iban"] == "***"
        assert metadata["changes"]["bank_iban"]["from"] == "***"
        assert metadata["before"]["name"] == "Alice"


# ---------------------------------------------------------------------------
# middleware.py
# ---------------------------------------------------------------------------

class TestMiddleware:
    def test_sets_and_clears_actor(self, user):
        from django_core_micha.auditlog.audit_context import get_current_actor_id
        from django_core_micha.auditlog.middleware import AuditlogActorMiddleware

        captured = {}

        def inner(request):
            captured["actor_id"] = get_current_actor_id()
            from django.http import HttpResponse
            return HttpResponse()

        mw = AuditlogActorMiddleware(inner)
        request = RequestFactory().get("/")
        request.user = user
        mw(request)

        assert captured["actor_id"] == user.pk
        assert get_current_actor_id() is None

    def test_sets_request_id_from_header(self):
        from django_core_micha.auditlog.audit_context import get_current_request_id
        from django_core_micha.auditlog.middleware import AuditlogActorMiddleware

        captured = {}

        def inner(request):
            captured["request_id"] = get_current_request_id()
            from django.http import HttpResponse
            return HttpResponse()

        mw = AuditlogActorMiddleware(inner)
        request = RequestFactory().get("/", HTTP_X_REQUEST_ID="my-req-id")
        request.user = AnonymousUser()
        mw(request)
        assert captured["request_id"] == "my-req-id"

    def test_generates_request_id_when_missing(self):
        from django_core_micha.auditlog.audit_context import get_current_request_id
        from django_core_micha.auditlog.middleware import AuditlogActorMiddleware

        captured = {}

        def inner(request):
            captured["request_id"] = get_current_request_id()
            from django.http import HttpResponse
            return HttpResponse()

        mw = AuditlogActorMiddleware(inner)
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        mw(request)
        assert captured["request_id"] is not None
        assert len(captured["request_id"]) == 32  # uuid4().hex


# ---------------------------------------------------------------------------
# prune_audit_events management command
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPruneCommand:
    def _make_event(self, days_old):
        from django_core_micha.auditlog.models import AuditEvent
        ev = AuditEvent.objects.create(event_type="test.event")
        AuditEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=days_old)
        )
        return ev

    def test_prunes_old_events(self):
        from django.core.management import call_command
        from django_core_micha.auditlog.models import AuditEvent
        old = self._make_event(800)
        recent = self._make_event(10)
        call_command("prune_audit_events", "--days=730", verbosity=0)
        assert not AuditEvent.objects.filter(pk=old.pk).exists()
        assert AuditEvent.objects.filter(pk=recent.pk).exists()

    def test_dry_run_does_not_delete(self):
        from django.core.management import call_command
        from django_core_micha.auditlog.models import AuditEvent
        old = self._make_event(800)
        call_command("prune_audit_events", "--days=730", "--dry-run", verbosity=0)
        assert AuditEvent.objects.filter(pk=old.pk).exists()

    @override_settings(AUDITLOG_RETENTION_DAYS=30)
    def test_uses_settings_default(self):
        from django.core.management import call_command
        from django_core_micha.auditlog.models import AuditEvent
        old = self._make_event(60)
        call_command("prune_audit_events", verbosity=0)
        assert not AuditEvent.objects.filter(pk=old.pk).exists()
