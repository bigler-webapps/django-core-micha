import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grant_views import ChatGroupGrantsView
from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.views import ChatDetailView


GROUPS = [{"id": "planning", "label": "Planning", "kind": "read", "members": [
    {"id": "plans", "label": "Plans", "available": True, "tools": ["read_plans"]},
]}]


def request(user, chat_id, method, data=None, view=ChatGroupGrantsView):
    req = getattr(APIRequestFactory(), method)("/", data, format="json")
    force_authenticate(req, user=user)
    return view.as_view()(req, chat_id=chat_id)


def selection(state, ids=None):
    return {"group_ids": ["planning"] if ids is None else ids,
            "grant_revision": state["grant_revision"],
            "catalogue_revision": state["catalogue_revision"]}


@pytest.mark.django_db
def test_private_owner_scope_forgery_and_stale_revisions():
    owner = get_user_model().objects.create_user(username="grant-api-owner")
    stranger = get_user_model().objects.create_user(username="grant-api-stranger")
    chat = AssistantChat.objects.create(owner=owner, scope="private")
    groups = copy.deepcopy(GROUPS)
    activate_catalogue(GROUPS, deployment="api", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="api",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: s == "private" and u.pk == owner.pk,
                           DCM_ASSISTANT_SHARED_SCOPE=lambda s: False):
        state = request(owner, chat.pk, "get").data
        assert request(stranger, chat.pk, "get").status_code == 404
        assert request(stranger, chat.pk, "put", selection(state)).status_code == 404
        for forged in ({**selection(state), "action_grants": ["plans"]},
                       {**selection(state), "scope": "private"},
                       selection(state, ["unknown"]), selection(state, ["planning", "planning"])):
            assert request(owner, chat.pk, "put", forged).status_code == 400
        groups[0]["members"][0]["available"] = False
        groups[0]["members"][0]["unavailable_reason"] = "Denied"
        assert request(owner, chat.pk, "put", selection(state)).status_code == 409
        groups[0]["members"][0]["available"] = True
        assert request(owner, chat.pk, "put", selection(state)).status_code == 200
        assert request(owner, chat.pk, "put", selection(state, [])).status_code == 409
        chat.refresh_from_db()
        assert chat.grant_revision == 1 and "planning" in chat.group_grants
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="api",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: False):
        assert request(owner, chat.pk, "get").status_code == 404
        assert request(owner, chat.pk, "put", selection(state)).status_code == 404
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="api",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: True,
                           DCM_ASSISTANT_SHARED_SCOPE=lambda s: True):
        assert request(owner, chat.pk, "get").status_code == 404
        assert request(owner, chat.pk, "put", selection(state)).status_code == 404


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first", ["archive", "save"])
def test_archive_and_grant_save_serialize_at_the_database(first):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL row locking")
    owner = get_user_model().objects.create_user(username="race-owner")
    chat = AssistantChat.objects.create(owner=owner)
    held = threading.Event()
    release = threading.Event()
    competing_pid = []

    def execute(operation):
        if operation == "archive":
            return request(owner, chat.pk, "patch", {"archived": True}, ChatDetailView)
        return request(owner, chat.pk, "put", body)

    def first_request():
        close_old_connections()
        try:
            with transaction.atomic():
                result = execute(first)
                held.set()
                if not release.wait(10):
                    raise TimeoutError("competing request did not reach the lock")
                return result.status_code
        finally:
            close_old_connections()

    def second_request():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                competing_pid.append(cursor.fetchone()[0])
            return execute("save" if first == "archive" else "archive").status_code
        finally:
            close_old_connections()

    activate_catalogue(GROUPS, deployment="api", expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="api",
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: copy.deepcopy(GROUPS)):
        body = selection(request(owner, chat.pk, "get").data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first_request)
            try:
                assert held.wait(10)
                b = pool.submit(second_request)
                deadline = time.monotonic() + 8
                blocked = False
                while time.monotonic() < deadline:
                    if competing_pid:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [competing_pid[0]])
                            blocked = cursor.fetchone()[0] > 0
                        if blocked:
                            break
                    time.sleep(0.02)
                assert blocked, "competing API request never waited on the row lock"
            finally:
                release.set()
            assert a.result(timeout=10) == 200
            assert b.result(timeout=10) == (409 if first == "archive" else 200)
        chat.refresh_from_db()
        assert chat.archived_at is not None
        if first == "archive":
            assert chat.group_grants is None and chat.grant_revision == 0
        else:
            assert "planning" in chat.group_grants and chat.grant_revision == 1
        assert request(owner, chat.pk, "put", body).status_code == 409
