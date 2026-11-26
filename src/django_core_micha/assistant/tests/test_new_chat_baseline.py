"""New-chat baseline snapshots and their serialization boundaries."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grants import catalogue, effective_ids, member_revision, validate_catalogue
from django_core_micha.assistant.models import AssistantCatalogueState, AssistantChat
from django_core_micha.assistant.group_grant_views import ChatGroupGrantsView
from django_core_micha.assistant.views import ChatListCreateView

GROUPS = [{"id": "g", "label": "Group", "kind": "read", "members": [
    {"id": "m", "label": "Member", "available": True, "tools": ["read"]},
]}]


def snapshot(generation, baseline=None):
    def pair(value):
        return {"global": 0, "user": value}
    return {"current": pair(generation), "baseline": None if baseline is None else pair(baseline)}


def activate():
    group = validate_catalogue(GROUPS)[0]
    identity = member_revision(group, group["members"][0])
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE={"g": {"m": identity}}):
        activate_catalogue(GROUPS, deployment="initial", expected_revision=0)


def config(lock, deployment="initial", groups=GROUPS):
    return override_settings(
        DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: groups,
        DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
        DCM_ASSISTANT_GROUP_DEPLOYMENT=deployment,
        DCM_ASSISTANT_LOCK_AUTHORIZATION_SNAPSHOT=lock,
    )


def create(user, family=False, body=None):
    request = APIRequestFactory().post("/", body or {}, format="json")
    force_authenticate(request, user=user)
    selection = {"provider": "p", "family": "f", "driver": "d", "model": "m", "source_id": "s"}
    with patch("django_core_micha.assistant.views.policy_enabled", return_value=False), \
         patch("django_core_micha.assistant.views.current_family_policy", return_value=family), \
         patch("django_core_micha.assistant.views.default_entry", return_value=selection), \
         patch("django_core_micha.assistant.views.validate_family", return_value=selection), \
         patch("django_core_micha.assistant.views.validate", return_value=selection):
        return ChatListCreateView.as_view()(request)


def grant_api(user, chat, reauthorize=False):
    factory = APIRequestFactory()
    def call(method, data=None):
        request = getattr(factory, method)("/", data, format="json")
        force_authenticate(request, user=user)
        return ChatGroupGrantsView.as_view()(request, chat_id=chat.pk)
    state = call("get")
    assert state.status_code == 200
    data = {"group_ids": ["g"], "grant_revision": state.data["grant_revision"],
            "catalogue_revision": state.data["catalogue_revision"]}
    if reauthorize:
        data["reauthorize_group_ids"] = ["g"]
    response = call("put", data)
    assert response.status_code == 200
    return response.data


@pytest.mark.django_db
@pytest.mark.parametrize("family", [False, True])
@pytest.mark.parametrize("cohort", [0, 1])
def test_new_account_and_immutable_old_cohort(family, cohort):
    activate()
    user = get_user_model().objects.create_user(username="new-account")
    old = AssistantChat.objects.create(owner=user)
    generation = [1]
    with config(lambda _: snapshot(generation[0], cohort)):
        response = create(user, family)
        assert response.status_code == 201
        new = AssistantChat.objects.get(pk=response.data["id"])
        assert new.group_grants is not None
        assert effective_ids(new, catalogue(new, user)) == {"m"}
        assert effective_ids(old, catalogue(old, user)) == ({"m"} if cohort == 1 else set())
        generation[0] = 2  # revoked
        assert effective_ids(new, catalogue(new, user)) == set()
        generation[0] = 3  # restored, but the previous grant remains stale
        assert effective_ids(new, catalogue(new, user)) == set()
        restored = create(user, family)
        assert restored.status_code == 201
        later = AssistantChat.objects.get(pk=restored.data["id"])
        assert later.group_grants["g"]["authorization"] == [0, 3]
        assert effective_ids(later, catalogue(later, user)) == {"m"}
        assert effective_ids(new, catalogue(new, user)) == set()
        assert effective_ids(old, catalogue(old, user)) == set()
        assert not grant_api(user, new)["groups"][0]["members"][0]["effective"]
        assert grant_api(user, new, reauthorize=True)["groups"][0]["members"][0]["effective"]
        old.refresh_from_db()
        assert old.group_grants is None


@pytest.mark.django_db
@pytest.mark.parametrize("family", [False, True])
def test_creation_freezes_current_eligibility(family):
    activate()
    user = get_user_model().objects.create_user(username="ineligible")
    unavailable = [{**GROUPS[0], "members": [{**GROUPS[0]["members"][0],
                    "available": False, "unavailable_reason": "No access"}]}]
    with config(lambda _: snapshot(1, 0), groups=unavailable):
        response = create(user, family)
        assert response.status_code == 201
        chat = AssistantChat.objects.get(pk=response.data["id"])
        assert chat.group_grants == {}
    with config(lambda _: snapshot(1, 0)):
        assert effective_ids(chat, catalogue(chat, user)) == set()


@pytest.mark.django_db
@pytest.mark.parametrize("family", [False, True])
def test_retirement_and_reintroduction_never_refresh_baseline(family):
    activate()
    user = get_user_model().objects.create_user(username="retirement")
    with config(lambda _: snapshot(0, 0)):
        before = create(user, family)
        assert before.status_code == 201
    activate_catalogue([], deployment="retired", expected_revision=1)
    activate_catalogue(GROUPS, deployment="returned", expected_revision=2)
    with config(lambda _: snapshot(0, 0), deployment="returned"):
        response = create(user, family)
        assert response.status_code == 201
        for pk in (before.data["id"], response.data["id"]):
            chat = AssistantChat.objects.get(pk=pk)
            assert effective_ids(chat, catalogue(chat, user)) == set()


@pytest.mark.django_db
@pytest.mark.parametrize("family", [False, True])
def test_missing_cohort_creates_empty_grant_and_allows_approval(family):
    activate()
    user = get_user_model().objects.create_user(username="missing-cohort")
    with config(lambda _: snapshot(0, None)):
        response = create(user, family)
        assert response.status_code == 201
        chat = AssistantChat.objects.get(pk=response.data["id"])
        assert chat.group_grants == {}
        assert effective_ids(chat, catalogue(chat, user)) == set()
        assert grant_api(user, chat)["groups"][0]["members"][0]["effective"]


@pytest.mark.django_db
@pytest.mark.parametrize("family", [False, True])
def test_missing_or_stale_proof_fails_closed(family):
    activate()
    user = get_user_model().objects.create_user(username="unverified")
    with config(None):
        assert create(user, family).status_code == 403
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=None):
        assert create(user, family).status_code == 403
    with config(lambda _: {"current": {"global": 0, "user": 0}}):
        assert create(user, family).status_code == 403
    with config(lambda _: {"current": None, "baseline": None}):
        assert create(user, family).status_code == 403
    with config(lambda _: snapshot(0, None), deployment="stale"):
        assert create(user, family).status_code == 403
    with config(lambda _: snapshot(0, 0), deployment="stale"):
        assert create(user, family).status_code == 403
    with config(lambda _: snapshot(0, None)), override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: "invalid"):
        assert create(user, family).status_code == 403
    AssistantCatalogueState.objects.filter(pk=1).update(baseline={"g": {"m": "invalid"}})
    with config(lambda _: snapshot(0, 0)):
        assert create(user, family).status_code == 403
    assert not AssistantChat.objects.filter(owner=user).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("family", [False, True])
def test_shared_and_unconfigured_chats_remain_null(family):
    user = get_user_model().objects.create_user(username="unchanged")
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=None):
        response = create(user, family)
        assert response.status_code == 201
        assert AssistantChat.objects.get(pk=response.data["id"]).group_grants is None
    activate()
    with config(None), override_settings(DCM_ASSISTANT_SHARED_SCOPE=lambda s: s == "shared",
                                          DCM_ASSISTANT_SCOPE_PERMISSION=lambda u, s: True):
        response = create(user, family, {"scope": "shared"})
        assert response.status_code == 201
        assert AssistantChat.objects.get(pk=response.data["id"]).group_grants is None


def wait_for_block(pid):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if pid:
            with connection.cursor() as cursor:
                cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [pid[0]])
                if cursor.fetchone()[0]:
                    return
        time.sleep(0.02)
    pytest.fail("creation did not wait for the serialization lock")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["authorization", "activation"])
@pytest.mark.parametrize("family", [False, True])
def test_creation_waits_for_concurrent_change(change, family):
    if connection.vendor != "postgresql":
        pytest.skip("requires PostgreSQL row locks")
    activate()
    user = get_user_model().objects.create_user(username="race", first_name="0")
    held, release = threading.Event(), threading.Event()
    pid = []

    def lock(uid):
        row = get_user_model().objects.select_for_update().get(pk=uid)
        return snapshot(int(row.first_name), 0)

    def mutate():
        close_old_connections()
        try:
            with transaction.atomic():
                if change == "authorization":
                    row = get_user_model().objects.select_for_update().get(pk=user.pk)
                    row.first_name = "1"
                    row.save(update_fields=["first_name"])
                else:
                    activate_catalogue(GROUPS, deployment="next", expected_revision=1)
                held.set()
                if not release.wait(10):
                    raise TimeoutError("creation was not released")
        finally:
            close_old_connections()

    def create_in_thread():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid.append(cursor.fetchone()[0])
            return create(user, family)
        finally:
            close_old_connections()

    with config(lock), ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(mutate)
        try:
            assert held.wait(10)
            creator = pool.submit(create_in_thread)
            wait_for_block(pid)
        finally:
            release.set()
        writer.result(timeout=10)
        response = creator.result(timeout=10)
    if change == "activation":
        assert response.status_code == 403
        assert not AssistantChat.objects.filter(owner=user).exists()
    else:
        assert response.status_code == 201
        chat = AssistantChat.objects.get(pk=response.data["id"])
        assert chat.group_grants["g"]["authorization"] == [0, 1]
        with config(lock):
            assert effective_ids(chat, catalogue(chat, user)) == {"m"}
