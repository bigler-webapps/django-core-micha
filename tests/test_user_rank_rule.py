import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.auth import roles
from django_core_micha.auth.permissions import (
    IsInviteAdminOrSuperuser,
    can_act_on_user,
)
from django_core_micha.auth.views import BaseUserViewSet


class _MinimalUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "first_name", "last_name", "is_active"]


class _UserViewSet(BaseUserViewSet):
    serializer_class = _MinimalUserSerializer


class _WideningUserViewSet(_UserViewSet):
    def get_queryset(self):
        return get_user_model().objects.all()


_crud_view = _UserViewSet.as_view(
    {"patch": "partial_update", "put": "update", "delete": "destroy"}
)
_wide_view = _WideningUserViewSet.as_view(
    {"patch": "partial_update", "put": "update", "delete": "destroy"}
)
_invite_view = _UserViewSet.as_view(
    {"get": "invite_link"},
    permission_classes=[IsAuthenticated, IsInviteAdminOrSuperuser],
)


def _request(method, path, user=None, data=None):
    request = getattr(APIRequestFactory(), method)(path, data)
    request.session = {}
    if user is not None:
        force_authenticate(request, user=user)
    return request


def _call_crud(method, actor, target, data=None):
    request = _request(method, f"/users/{target.pk}/", actor, data)
    return _crud_view(request, pk=target.pk)


def _call_invite(actor, target):
    request = _request("get", f"/users/{target.pk}/invite-link/", actor)
    return _invite_view(request, pk=target.pk)


@pytest.fixture(autouse=True)
def _role_settings(settings, monkeypatch):
    settings.ROLE_DEFINITIONS = {
        "user": {"level": 1, "label": "User"},
        "manager": {"level": 2, "label": "Manager"},
        "senior": {"level": 3, "label": "Senior"},
    }
    settings.DEFAULT_ROLE_CODE = "user"
    settings.INVITE_MIN_ROLE_LEVEL = 2
    settings.SECURITY_LEVELS = {"basic": 0, "strong": 1}
    settings.SECURITY_DEFAULT_LEVEL = "basic"
    role_codes = {}
    monkeypatch.setattr(
        roles,
        "get_role_code_for_user",
        lambda user: role_codes.get(getattr(user, "username", None), "user"),
    )
    return role_codes


@pytest.fixture
def users(db, _role_settings):
    role_codes = _role_settings

    def create(username, role="user", *, superuser=False, last_login=None):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpassword123",
            is_superuser=superuser,
        )
        if last_login is not None:
            user.last_login = last_login
            user.save(update_fields=["last_login"])
        role_codes[username] = role
        return user

    return create


def _state(user):
    user.refresh_from_db()
    return user.username, user.first_name, user.last_name, user.is_active


@pytest.mark.django_db
@pytest.mark.parametrize("method,data", [
    ("patch", {"is_active": False}),
    ("patch", {"first_name": "Changed"}),
    ("put", {"first_name": "Changed"}),
    ("delete", None),
])
def test_level_two_cannot_act_on_higher_rank(users, method, data):
    actor = users("manager", "manager")
    target = users("senior", "senior")
    before = _state(target)

    response = _call_crud(method, actor, target, data)

    assert response.status_code == 403
    assert _state(target) == before
    assert get_user_model().objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_level_two_cannot_get_invite_link_for_higher_rank(users):
    actor = users("manager", "manager")
    target = users("senior", "senior")
    before = _state(target)

    response = _call_invite(actor, target)

    assert response.status_code == 403
    assert response.data == {"code": "Auth.RANK_INSUFFICIENT"}
    assert _state(target) == before


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["superuser", "equal"])
def test_level_two_cannot_act_on_superuser_or_equal_rank(users, kind):
    actor = users("manager", "manager")
    if kind == "superuser":
        target = users("root", "senior", superuser=True)
    else:
        target = users("peer", "manager")
    before = _state(target)

    for method, data in [
        ("patch", {"is_active": False}),
        ("patch", {"first_name": "Changed"}),
        ("put", {"first_name": "Changed"}),
        ("delete", None),
    ]:
        response = _call_crud(method, actor, target, data)
        assert response.status_code == 403
        assert _state(target) == before
        assert get_user_model().objects.filter(pk=target.pk).exists()

    response = _call_invite(actor, target)
    assert response.status_code == 403
    assert response.data == {"code": "Auth.RANK_INSUFFICIENT"}
    assert _state(target) == before


@pytest.mark.django_db
def test_higher_rank_can_act_on_lower_rank_and_invite_never_logged_in(users):
    actor = users("senior", "senior")
    target = users("manager", "manager")

    response = _call_invite(actor, target)
    assert response.status_code == 200
    assert "invite_link" in response.data

    response = _call_crud("patch", actor, target, {"is_active": False})
    assert response.status_code == 200
    target.refresh_from_db()
    assert target.is_active is False

    response = _call_crud(
        "put", actor, target, {"username": target.username, "first_name": "Replaced"}
    )
    assert response.status_code == 200
    target.refresh_from_db()
    assert target.first_name == "Replaced"

    response = _call_crud("delete", actor, target)
    assert response.status_code == 204
    assert not get_user_model().objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("actor_role", ["senior", "superuser"])
def test_logged_in_target_requires_self_service_password_reset(users, actor_role):
    actor = users(
        f"actor-{actor_role}", "senior", superuser=(actor_role == "superuser")
    )
    target = users("logged-in", "manager", last_login=timezone.now())

    response = _call_invite(actor, target)

    assert response.status_code == 409
    assert response.data == {"code": "Auth.USE_PASSWORD_RESET"}
    assert get_user_model().objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_rank_failure_precedes_logged_in_reset_code(users):
    actor = users("manager", "manager")
    target = users("logged-in-senior", "senior", last_login=timezone.now())

    response = _call_invite(actor, target)

    assert response.status_code == 403
    assert response.data == {"code": "Auth.RANK_INSUFFICIENT"}


@pytest.mark.django_db
def test_superuser_can_update_delete_and_invite_non_superuser(users):
    actor = users("root", "user", superuser=True)
    target = users("ordinary", "senior")

    response = _call_invite(actor, target)
    assert response.status_code == 200
    assert "invite_link" in response.data

    response = _call_crud("patch", actor, target, {"first_name": "Updated"})
    assert response.status_code == 200
    target.refresh_from_db()
    assert target.first_name == "Updated"

    response = _call_crud(
        "put", actor, target, {"username": target.username, "first_name": "Replaced"}
    )
    assert response.status_code == 200
    target.refresh_from_db()
    assert target.first_name == "Replaced"

    response = _call_crud("delete", actor, target)
    assert response.status_code == 204
    assert not get_user_model().objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_level_two_self_edit_rules_remain_in_force(users):
    actor = users("manager", "manager")

    response = _call_crud("patch", actor, actor, {"first_name": "Self"})
    assert response.status_code == 200
    actor.refresh_from_db()
    assert actor.first_name == "Self"

    response = _call_crud("patch", actor, actor, {"is_active": False})
    assert response.status_code == 200
    actor.refresh_from_db()
    assert actor.is_active is False


@pytest.mark.django_db
def test_non_admin_widened_queryset_keeps_consumer_authority(users):
    actor = users("ordinary", "user")
    target = users("other-ordinary", "user")
    request = _request("patch", f"/users/{target.pk}/", actor, {"first_name": "Changed"})

    response = _wide_view(request, pk=target.pk)

    assert response.status_code == 200
    target.refresh_from_db()
    assert target.first_name == "Changed"


@pytest.mark.django_db
def test_unauthenticated_user_cannot_act_or_get_invite_link(users):
    target = users("target", "user")
    assert can_act_on_user(None, target) is False

    response = _invite_view(_request("get", f"/users/{target.pk}/invite-link/"), pk=target.pk)

    assert response.status_code in (401, 403)
    assert get_user_model().objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_can_act_on_user_rank_rules(users):
    ordinary = users("ordinary", "user")
    manager = users("manager", "manager")
    senior = users("senior", "senior")
    root = users("root", "user", superuser=True)
    root_target = users("root-target", "user", superuser=True)

    assert can_act_on_user(root, senior) is True
    assert can_act_on_user(manager, root_target) is False
    assert can_act_on_user(senior, manager) is True
    assert can_act_on_user(manager, manager) is False
    assert can_act_on_user(ordinary, manager) is False
    assert can_act_on_user(senior, ordinary) is True
    assert can_act_on_user(None, ordinary) is False


@pytest.mark.django_db
def test_non_superuser_cannot_get_an_invite_link_for_themselves(users):
    manager = users("self-link-manager", "manager")
    response = _call_invite(manager, manager)
    assert response.status_code == 403 and response.data == {"code": "Auth.RANK_INSUFFICIENT"}


@pytest.mark.django_db
def test_the_rank_guard_is_what_decides_between_equal_and_lower_targets(users):
    senior = users("rank-senior", "senior")
    lower = users("rank-lower", "manager")
    equal = users("rank-equal", "senior")
    assert _call_crud("patch", senior, lower, {"first_name": "Ok"}).status_code == 200
    assert _call_crud("patch", senior, equal, {"first_name": "No"}).status_code == 403
    equal.refresh_from_db()
    assert equal.first_name != "No"
