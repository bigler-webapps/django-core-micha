import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.auth import roles
from django_core_micha.auth.policy import get_or_create_auth_policy
from django_core_micha.auth.views import BaseUserViewSet


class _MinimalUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "first_name"]
        extra_kwargs = {
            "username": {"required": False, "allow_blank": True},
            "email": {"required": False, "allow_blank": True},
        }

    def create(self, validated_data):
        if not validated_data.get("username"):
            validated_data["username"] = validated_data["email"]
        return super().create(validated_data)


class _UserViewSet(BaseUserViewSet):
    serializer_class = _MinimalUserSerializer


class _WidenCreateViewSet(_UserViewSet):
    def can_create_user(self, request):
        return True


class _NarrowCreateViewSet(_UserViewSet):
    def can_create_user(self, request):
        return False


def _blocked(email):
    return isinstance(email, str) and email.startswith("blocked")


class _CreatePermission(BasePermission):
    def has_permission(self, request, view):
        data = request.data if hasattr(request.data, "get") else {}
        return view.action == "create" and request.user.is_authenticated and not _blocked(data.get("email"))


class _PermissionOverrideViewSet(_UserViewSet):
    def get_permissions(self):
        if getattr(self, "action", None) == "create":
            return [_CreatePermission()]
        return super().get_permissions()


class _PerformCreateGuardViewSet(_UserViewSet):
    def perform_create(self, serializer):
        if roles.get_role_level_for_user(self.request.user) < 2 or _blocked(serializer.validated_data.get("email")):
            raise PermissionDenied("Permission denied.")
        serializer.save()


_default_view = _UserViewSet.as_view({"post": "create"})
_widen_view = _WidenCreateViewSet.as_view({"post": "create"})
_narrow_view = _NarrowCreateViewSet.as_view({"post": "create"})
_permission_override_view = _PermissionOverrideViewSet.as_view({"post": "create"})
_perform_guard_view = _PerformCreateGuardViewSet.as_view({"post": "create"})


@pytest.fixture(autouse=True)
def _role_settings(settings, monkeypatch):
    settings.ROLE_DEFINITIONS = {
        "user": {"level": 1, "label": "User"},
        "manager": {"level": 2, "label": "Manager"},
        "admin": {"level": 3, "label": "Admin"},
    }
    settings.DEFAULT_ROLE_CODE = "user"
    settings.INVITE_MIN_ROLE_LEVEL = 2
    settings.INVITE_ADMIN_ROLES = ()
    settings.AUTH_POLICY_MODEL = "testapp.AuthPolicyStub"
    settings.SECURITY_LEVELS = {"basic": 0, "strong": 1}
    settings.SECURITY_DEFAULT_LEVEL = "basic"
    settings.SECURITY_ENFORCE_STRONG_AUTH = False
    cache.clear()
    role_codes = {}
    monkeypatch.setattr(
        roles,
        "get_role_code_for_user",
        lambda user: role_codes.get(getattr(user, "username", None), "user"),
    )
    return role_codes


@pytest.fixture
def make_user(db, _role_settings):
    def create(username, role="user", *, superuser=False):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpassword123",
            is_superuser=superuser,
        )
        _role_settings[username] = role
        return user

    return create


def _create(view, actor, data=None, *, security_level=None):
    request = APIRequestFactory().post("/api/users/", data or {}, format="json")
    request.session = {}
    if security_level is not None:
        request.session["auth_level"] = security_level
    if actor is not None:
        force_authenticate(request, user=actor)
    return view(request)


def _count():
    return get_user_model().objects.count()


@pytest.mark.django_db
@pytest.mark.parametrize("payload", [{"username": "blocked"}, {}])
def test_non_admin_create_is_refused_before_payload_validation(make_user, payload):
    actor = make_user("ordinary")
    before = _count()

    response = _create(_default_view, actor, payload)

    assert response.status_code == 403
    assert _count() == before


@pytest.mark.django_db
def test_unauthenticated_create_is_refused(make_user):
    make_user("existing")
    before = _count()

    response = _create(_default_view, None, {"username": "anonymous"})

    assert response.status_code in (401, 403)
    assert _count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("role,superuser", [("manager", False), ("user", True)])
def test_admin_and_superuser_create_email_or_username(make_user, role, superuser):
    actor = make_user(f"actor-{role}-{superuser}", role, superuser=superuser)

    email_response = _create(
        _default_view, actor, {"email": f"new-{role}-{superuser}@example.com"}
    )
    assert email_response.status_code == 201
    assert get_user_model().objects.filter(
        email=f"new-{role}-{superuser}@example.com"
    ).exists()

    username_response = _create(
        _default_view,
        actor,
        {"username": f"named-{role}-{superuser}"},
    )
    assert username_response.status_code == 201
    assert get_user_model().objects.filter(
        username=f"named-{role}-{superuser}"
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [{}, {"username": "  ", "email": " \t "}, {"username": 7, "email": None}],
)
@pytest.mark.parametrize("superuser", [False, True])
def test_permitted_empty_identity_returns_field_errors_without_write(
    make_user, payload, superuser
):
    actor = make_user("empty-identity-actor", "manager", superuser=superuser)
    before = _count()

    response = _create(_default_view, actor, payload)

    assert response.status_code == 400
    assert "username" in response.data
    assert "email" in response.data
    assert _count() == before


@pytest.mark.django_db
def test_admin_auth_policy_requires_strong_request_session(settings, make_user):
    settings.AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 3
    admin = make_user("full-admin", "admin")
    policy = get_or_create_auth_policy()
    policy.admin_required_auth_factor_count = 2
    policy.save()
    before = _count()

    weak = _create(
        _default_view, admin, {"email": "weak@example.com"}, security_level="basic"
    )
    assert weak.status_code == 403
    assert _count() == before

    strong = _create(
        _default_view, admin, {"email": "strong@example.com"}, security_level="strong"
    )
    assert strong.status_code == 201
    assert get_user_model().objects.filter(email="strong@example.com").exists()


@pytest.mark.django_db
def test_create_hook_can_widen_and_narrow(make_user):
    ordinary = make_user("ordinary")
    before = _count()
    widened = _create(_widen_view, ordinary, {"email": "widened@example.com"})
    assert widened.status_code == 201
    assert get_user_model().objects.filter(email="widened@example.com").exists()

    for actor in [make_user("manager", "manager"), make_user("root", superuser=True)]:
        before = _count()
        refused = _create(_narrow_view, actor, {"email": "narrowed@example.com"})
        assert refused.status_code == 403
        assert _count() == before


@pytest.mark.django_db
def test_consumer_permission_and_perform_create_guards_still_apply(make_user):
    manager = make_user("manager", "manager")
    ordinary = make_user("ordinary")

    for view in (_permission_override_view, _perform_guard_view):
        allowed = _create(view, manager, {"email": f"allowed-{view.__name__}@example.com"})
        assert allowed.status_code == 201
        before = _count()
        # the consumer guard itself decides here (the users admin passes the default hook, the guard refuses)
        guarded = _create(view, manager, {"email": f"blocked-{view.__name__}@example.com"})
        assert guarded.status_code == 403 and _count() == before
        refused = _create(view, ordinary, {"email": f"refused-{view.__name__}@example.com"})
        assert refused.status_code == 403
        assert _count() == before


def test_removed_security_helpers_are_not_importable(settings):
    with pytest.raises(ImportError):
        from django_core_micha.auth.permissions import RequireStrongSecurity  # noqa: F401
    with pytest.raises(ImportError):
        from django_core_micha.auth.security import require_security_level  # noqa: F401

    from django.conf import settings
    from django_core_micha.auth.security import is_level_sufficient

    assert hasattr(settings, "SECURITY_ENFORCE_STRONG_AUTH")
    assert callable(is_level_sufficient)


@pytest.mark.django_db
@pytest.mark.parametrize("body", [["a-list"], "a-string", 7])
def test_a_non_object_json_body_is_an_empty_payload_not_a_server_error(make_user, body):
    admin = make_user("body-admin", "manager")
    before = _count()
    request = APIRequestFactory().post("/api/users/", body, format="json")
    request.session = {}
    force_authenticate(request, user=admin)
    response = _default_view(request)
    assert response.status_code == 400 and _count() == before
    assert "username" in response.data and "email" in response.data  # the field errors, not a bare 400
