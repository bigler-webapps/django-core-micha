import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.auth import roles
from django_core_micha.auth.access import (
    _default_auth_policy_write_level,
    has_full_auth_admin_rights,
    has_invite_admin_rights,
)
from django_core_micha.auth.permissions import can_view_invite_admin, can_view_users_admin
from django_core_micha.auth.policy import get_or_create_auth_policy
from django_core_micha.auth.views import BaseUserViewSet


class _AuthPolicyViewSet(BaseUserViewSet):
    pass


_auth_policy_view = _AuthPolicyViewSet.as_view(
    {"get": "auth_policy", "put": "auth_policy", "patch": "auth_policy"}
)


@pytest.fixture(autouse=True)
def _auth_policy_settings(settings):
    settings.AUTH_POLICY_MODEL = "testapp.AuthPolicyStub"
    settings.SECURITY_LEVELS = {"basic": 0, "strong": 1}
    settings.SECURITY_DEFAULT_LEVEL = "basic"
    settings.DEFAULT_ROLE_CODE = "user"
    settings.INVITE_MIN_ROLE_LEVEL = 2
    settings.INVITE_ADMIN_ROLES = ()
    settings.AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = None
    # A None override is not equivalent to an absent override for this setting.
    del settings.AUTH_POLICY_WRITE_MIN_ROLE_LEVEL
    cache.clear()


@pytest.fixture
def role_codes(monkeypatch):
    codes = {}
    monkeypatch.setattr(
        roles,
        "get_role_code_for_user",
        lambda user: codes.get(
            getattr(user, "username", None), roles.get_default_role_code()
        ),
    )
    return codes


@pytest.fixture
def make_user(db, role_codes):
    def create(username, role=None, *, superuser=False):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpassword123",
            is_superuser=superuser,
        )
        if role is not None:
            role_codes[username] = role
        return user

    return create


def _policy_request(method, user, data=None):
    request = getattr(APIRequestFactory(), method)("/auth-policy/", data)
    request.session = {}
    force_authenticate(request, user=user)
    return _auth_policy_view(request)


def _policy_values(policy):
    policy.refresh_from_db()
    return policy.allow_self_signup_open, policy.allow_admin_invite


def _assert_no_admin_rights(user):
    assert has_full_auth_admin_rights(user) is False
    assert has_invite_admin_rights(user) is False
    assert can_view_users_admin(user) is False
    assert can_view_invite_admin(user) is False


@pytest.mark.django_db
def test_default_roles_deny_roleless_user_and_superuser_can_read_and_write(make_user):
    user = make_user("roleless")
    _assert_no_admin_rights(user)

    policy = get_or_create_auth_policy()
    before = _policy_values(policy)
    assert _policy_request("get", user).status_code == 403
    assert _policy_request("put", user, {"allow_self_signup_open": True}).status_code == 403
    assert _policy_values(policy) == before

    root = make_user("root", superuser=True)
    assert _policy_request("get", root).status_code == 200
    response = _policy_request("put", root, {"allow_self_signup_open": True})
    assert response.status_code == 200
    assert _policy_values(policy) == (True, before[1])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "definitions,default_code",
    [
        ({"user": {"level": 1}}, "user"),
        ({"user": {"level": 1}, "member": {"level": 1}}, "user"),
        ({"member": {"level": 2}, "manager": {"level": 2}}, "manager"),
    ],
)
def test_flat_role_definitions_deny_admin_rights_and_auth_policy(
    settings, make_user, definitions, default_code
):
    settings.ROLE_DEFINITIONS = definitions
    settings.DEFAULT_ROLE_CODE = default_code
    user = make_user(f"flat-{default_code}")
    assert has_full_auth_admin_rights(user) is False
    if default_code == "user":
        _assert_no_admin_rights(user)

    policy = get_or_create_auth_policy()
    before = _policy_values(policy)
    # A level-2 default role can still read through INVITE_MIN_ROLE_LEVEL;
    # preserve that separate permission while checking the auth-policy write gate.
    if default_code == "user":
        assert _policy_request("get", user).status_code == 403
    assert _policy_request("put", user, {"allow_self_signup_open": True}).status_code == 403
    assert _policy_values(policy) == before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "definitions,admin_role,ordinary_role",
    [
        ({"user": {"level": 1}, "admin": {"level": 3}}, "admin", "user"),
        (
            {"user": {"level": 1}, "manager": {"level": 2}, "owner": {"level": 4}},
            "owner",
            "manager",
        ),
    ],
)
def test_hierarchical_roles_keep_the_highest_level_threshold(
    settings, make_user, definitions, admin_role, ordinary_role
):
    settings.ROLE_DEFINITIONS = definitions
    admin = make_user(f"admin-{admin_role}", admin_role)
    ordinary = make_user(f"ordinary-{ordinary_role}", ordinary_role)

    assert has_full_auth_admin_rights(admin) is True
    assert has_full_auth_admin_rights(ordinary) is False
    assert _policy_request("get", admin).status_code == 200
    policy = get_or_create_auth_policy()
    before = _policy_values(policy)
    assert _policy_request("put", ordinary, {"allow_self_signup_open": True}).status_code == 403
    assert _policy_values(policy) == before  # a refused write changes nothing
    assert _policy_request("put", admin, {"allow_self_signup_open": True}).status_code == 200
    assert _policy_values(policy) == (True, before[1])  # the admin write is effective


@pytest.mark.django_db
def test_explicit_threshold_wins_below_computed_default(settings, make_user):
    settings.AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 1
    user = make_user("explicit-low")
    assert has_full_auth_admin_rights(user) is True
    assert _policy_request("get", user).status_code == 200


@pytest.mark.django_db
def test_explicit_threshold_wins_above_computed_default(settings, make_user):
    settings.ROLE_DEFINITIONS = {"user": {"level": 1}, "admin": {"level": 3}}
    settings.AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 4
    admin = make_user("explicit-high", "admin")
    assert has_full_auth_admin_rights(admin) is False
    policy = get_or_create_auth_policy()
    before = _policy_values(policy)
    assert _policy_request("put", admin, {"allow_self_signup_open": True}).status_code == 403
    assert _policy_values(policy) == before


@pytest.mark.parametrize(
    "definitions,default_code,expected",
    [
        ("absent", "user", 3),
        ({}, "user", 3),
        ({"user": {"level": 1}}, "user", 3),
        ({"user": {"level": 1}, "member": {"level": 1}}, "user", 3),
        ({"manager": {"level": 2}, "lead": {"level": 2}}, "manager", 3),
        ({"user": {"level": "bad"}}, "user", 3),
        ({"user": {"level": 1}, "admin": {"level": 3}}, "user", 3),
        ({"user": {"level": 1}, "manager": {"level": 2}}, "user", 2),
        (
            {"user": {"level": 1}, "manager": {"level": 2}, "owner": {"level": 4}},
            "user",
            4,
        ),
        ({"user": {"level": 1}}, "missing-default", 3),
        ({"user": {"level": 1}, "admin": {"level": 3}}, "missing-default", 3),  # a real hierarchy keeps its top level
        ({"admin": {"level": 3}}, "admin", 4),
        ({"owner": {"level": 4}}, "owner", 5),
        ({"user": {"level": 1}, "admin": {"level": 3}}, "admin", 4),
    ],
)
def test_default_threshold_computations(settings, definitions, default_code, expected):
    settings.DEFAULT_ROLE_CODE = default_code
    if definitions == "absent":
        if hasattr(settings, "ROLE_DEFINITIONS"):
            del settings.ROLE_DEFINITIONS
    else:
        settings.ROLE_DEFINITIONS = definitions

    assert _default_auth_policy_write_level() == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "definitions,default_code",
    [
        ({"admin": {"level": 3}}, "admin"),
        ({"owner": {"level": 4}}, "owner"),
        ({"user": {"level": 1}}, "missing-default"),
    ],
)
def test_flat_setups_at_or_above_level_three_or_with_a_wrong_default_code_stay_closed(
    settings, make_user, definitions, default_code
):
    settings.ROLE_DEFINITIONS = definitions
    settings.DEFAULT_ROLE_CODE = default_code
    for role in definitions:  # a user assigned any defined role, and a role-less one
        assert has_full_auth_admin_rights(make_user(f"flat-any-{role}", role)) is False
    assert has_full_auth_admin_rights(make_user("flat-roleless")) is False
    policy = get_or_create_auth_policy()
    before = _policy_values(policy)
    assert _policy_request("put", make_user("flat-writer", next(iter(definitions))), {"allow_self_signup_open": True}).status_code == 403
    assert _policy_values(policy) == before
    root = make_user("flat-root", superuser=True)
    assert _policy_request("put", root, {"allow_self_signup_open": True}).status_code == 200
