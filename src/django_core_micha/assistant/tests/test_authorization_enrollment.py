"""Contract examples; the app must independently prove its real lock coverage."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, transaction
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grants import catalogue, effective_ids, member_revision, save_selection, validate_catalogue
from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.provider_context import context_token

GROUPS = [{"id": "g", "label": "Group", "kind": "read", "members": [
    {"id": "m", "label": "Member", "available": True, "tools": ["read"]},
]}]


def settings_for(hook):
    return override_settings(
        DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: GROUPS,
        DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
        DCM_ASSISTANT_GROUP_DEPLOYMENT="enrollment",
        DCM_ASSISTANT_LOCK_AUTHORIZATION_SNAPSHOT=hook,
    )


def snapshot(current, baseline):
    def pair(n):
        return {"global": 0, "user": n}
    return {"current": pair(current), "baseline": None if baseline is None else pair(baseline)}


@pytest.mark.django_db
def test_enrollment_revoke_restore_and_explicit_reauthorization():
    normalized = validate_catalogue(GROUPS)
    identity = member_revision(normalized[0], normalized[0]["members"][0])
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE={"g": {"m": identity}}):
        activate_catalogue(GROUPS, deployment="enrollment", expected_revision=0)
    user = get_user_model().objects.create_user(username="generation")
    chat = AssistantChat.objects.create(owner=user)
    values = [0, None]
    with settings_for(lambda _: snapshot(*values)):
        assert effective_ids(chat, catalogue(chat, user)) == set()
        values[0] = 1
        # First access did not create or bind a baseline.
        assert effective_ids(chat, catalogue(chat, user)) == set()
    enrolled = get_user_model().objects.create_user(username="enrolled-generation")
    chat = AssistantChat.objects.create(owner=enrolled)
    values[:] = [0, 0]
    with settings_for(lambda _: snapshot(*values)):
        assert effective_ids(chat, catalogue(chat, enrolled)) == {"m"}
        save_selection(chat, catalogue(chat, enrolled), ["g"])
        before = context_token(chat.pk, enrolled)
        values[0] = 1
        assert effective_ids(chat, catalogue(chat, enrolled)) == set()
        values[0] = 2  # restored eligibility must not restore an old grant
        assert context_token(chat.pk, enrolled) != before
        assert effective_ids(chat, catalogue(chat, enrolled)) == set()
        save_selection(chat, catalogue(chat, enrolled), ["g"])
        assert effective_ids(chat, catalogue(chat, enrolled)) == set()
        save_selection(chat, catalogue(chat, enrolled), ["g"], ["g"])
        assert effective_ids(chat, catalogue(chat, enrolled)) == {"m"}


@pytest.mark.django_db
def test_missing_or_malformed_app_contract_denies():
    activate_catalogue(GROUPS, deployment="enrollment", expected_revision=0)
    user = get_user_model().objects.create_user(username="missing-enrollment-hook")
    chat = AssistantChat.objects.create(owner=user)
    for hook in (None, lambda _: {"current": {"global": 0, "user": 0}}):
        with settings_for(hook), pytest.raises(PermissionDenied):
            catalogue(chat, user)


@pytest.mark.django_db(transaction=True)
def test_save_waits_for_app_revoke_and_rejects_stale_snapshot():
    if connection.vendor != "postgresql":
        pytest.skip("requires independent PostgreSQL transactions")
    user = get_user_model().objects.create_user(username="app-lock-race", first_name="0")
    chat = AssistantChat.objects.create(owner=user)
    activate_catalogue(GROUPS, deployment="enrollment", expected_revision=0)
    held, release, started = threading.Event(), threading.Event(), threading.Event()
    save_pid = []

    def lock(uid):
        row = get_user_model().objects.select_for_update().get(pk=uid)
        return snapshot(int(row.first_name), 0)

    def revoke():
        close_old_connections()
        try:
            with transaction.atomic():
                row = get_user_model().objects.select_for_update().get(pk=user.pk)
                held.set()
                if not release.wait(10):
                    raise TimeoutError("revoke lock not released")
                row.first_name = "1"
                row.save(update_fields=["first_name"])
        finally:
            close_old_connections()

    def save(old):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                save_pid.append(cursor.fetchone()[0])
            started.set()
            with pytest.raises(PermissionDenied):
                save_selection(chat, old, ["g"])
        finally:
            close_old_connections()

    with settings_for(lock):
        old = catalogue(chat, user)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(revoke)
            assert held.wait(10)
            saver = pool.submit(save, old)
            try:
                assert started.wait(10)
                deadline = time.monotonic() + 8
                blocked = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [save_pid[0]])
                        blocked = cursor.fetchone()[0] > 0
                    if blocked:
                        break
                    time.sleep(0.05)
                assert blocked, "save did not wait on the app lock"
            finally:
                release.set()
            writer.result(timeout=10)
            saver.result(timeout=10)
        chat.refresh_from_db()
        assert chat.group_grants is None
