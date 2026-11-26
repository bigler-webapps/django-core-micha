"""PostgreSQL evidence for the catalogue activation/save lock ordering."""
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


GROUPS = [{"id": "g", "label": "Group", "kind": "read", "members": [
    {"id": "m", "label": "Member", "available": True, "tools": ["read"]},
]}]


def request(user, chat_id, method, data=None):
    req = getattr(APIRequestFactory(), method)("/", data, format="json")
    force_authenticate(req, user=user)
    return ChatGroupGrantsView.as_view()(req, chat_id=chat_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first", ["activation", "save"])
def test_activation_and_grant_save_serialize(first):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL row locks")
    owner = get_user_model().objects.create_user(username="activation-save-race")
    chat = AssistantChat.objects.create(owner=owner)
    activate_catalogue(GROUPS, deployment="old", expected_revision=0)
    held, release = threading.Event(), threading.Event()
    competing_pid = []

    def execute(operation):
        if operation == "activation":
            return activate_catalogue(GROUPS, deployment="new", expected_revision=1)
        return request(owner, chat.pk, "put", body).status_code

    def first_operation():
        close_old_connections()
        try:
            with transaction.atomic():
                result = execute(first)
                held.set()
                if not release.wait(10):
                    raise TimeoutError("competing operation did not reach the lock")
                return result
        finally:
            close_old_connections()

    def second_operation():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                competing_pid.append(cursor.fetchone()[0])
            return execute("save" if first == "activation" else "activation")
        finally:
            close_old_connections()

    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: copy.deepcopy(GROUPS),
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="old"):
        state = request(owner, chat.pk, "get").data
        body = {"group_ids": ["g"], "grant_revision": state["grant_revision"],
                "catalogue_revision": state["catalogue_revision"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first_operation)
            try:
                assert held.wait(10)
                b = pool.submit(second_operation)
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
                assert blocked, "competing operation did not wait on the catalogue lock"
            finally:
                release.set()
            assert a.result(timeout=10) == (2 if first == "activation" else 200)
            assert b.result(timeout=10) == (403 if first == "activation" else 2)
    chat.refresh_from_db()
    assert chat.grant_revision == (0 if first == "activation" else 1)
    if first == "activation":
        assert chat.group_grants is None
    else:
        assert "g" in chat.group_grants
