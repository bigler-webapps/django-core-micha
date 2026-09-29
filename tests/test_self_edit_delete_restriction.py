import pytest
from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.auth.views import BaseUserViewSet


class _MinimalUserSerializer(serializers.ModelSerializer):
    # The real BaseUserSerializer's `can_manage`/`ui_permissions` fields read
    # `user.profile`, which needs a concrete Profile model wired to
    # AUTH_USER_MODEL -- out of scope here (this repo's test settings run
    # against the bare django.contrib.auth.User). This viewset restriction is
    # about the generic update/partial_update/destroy route, not serializer
    # output, so a minimal serializer over native User fields is enough to
    # exercise it.
    class Meta:
        model = get_user_model()
        fields = ["id", "first_name", "last_name", "is_active"]


class _UserViewSet(BaseUserViewSet):
    serializer_class = _MinimalUserSerializer


class _WideningUserViewSet(BaseUserViewSet):
    serializer_class = _MinimalUserSerializer

    def get_queryset(self):
        # Simulates a consumer like jg-ferien: a non-admin can still reach
        # every user through this consumer's deliberately wider queryset.
        return get_user_model().objects.all()


_view = _UserViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
)
_wide_view = _WideningUserViewSet.as_view(
    {"patch": "partial_update", "put": "update", "delete": "destroy"}
)


def _authed(method, path, user, data=None):
    request = getattr(APIRequestFactory(), method)(path, data)
    request.session = {}
    force_authenticate(request, user=user)
    return request


@pytest.fixture(autouse=True)
def _security_level_settings(settings):
    settings.SECURITY_LEVELS = {"basic": 0, "strong": 1}
    settings.SECURITY_DEFAULT_LEVEL = "basic"
    # Without ROLE_DEFINITIONS, access.py's fallback
    # (_default_auth_policy_write_level = max of all defined levels) collapses
    # to a single "user" role at level 1, which then also satisfies its own
    # admin-write threshold -- every plain user reads as an admin. A second
    # role at a higher level is required for the non-admin fixture below to
    # actually be non-admin.
    settings.ROLE_DEFINITIONS = {
        "user": {"level": 1, "label": "User"},
        "admin": {"level": 3, "label": "Admin"},
    }


@pytest.fixture
def non_admin(db):
    return get_user_model().objects.create_user(
        username="self-edit-user",
        email="self-edit@example.com",
        password="testpassword123",
    )


@pytest.fixture
def other_non_admin(db):
    return get_user_model().objects.create_user(
        username="other-user",
        email="other@example.com",
        password="testpassword123",
    )


@pytest.fixture
def admin(db):
    return get_user_model().objects.create_user(
        username="admin-user",
        email="admin@example.com",
        password="testpassword123",
        is_superuser=True,
    )


@pytest.mark.django_db
def test_non_admin_generic_patch_is_active_on_self_is_rejected(non_admin):
    request = _authed("patch", f"/users/{non_admin.pk}/", non_admin, {"is_active": False})
    response = _view(request, pk=non_admin.pk)

    assert response.status_code == 400
    non_admin.refresh_from_db()
    assert non_admin.is_active is True


@pytest.mark.django_db
def test_non_admin_generic_patch_allowlisted_field_on_self_still_works(non_admin):
    request = _authed("patch", f"/users/{non_admin.pk}/", non_admin, {"first_name": "Changed"})
    response = _view(request, pk=non_admin.pk)

    assert response.status_code == 200
    non_admin.refresh_from_db()
    assert non_admin.first_name == "Changed"


@pytest.mark.django_db
def test_non_admin_generic_delete_on_self_is_refused(non_admin):
    request = _authed("delete", f"/users/{non_admin.pk}/", non_admin)
    response = _view(request, pk=non_admin.pk)

    assert response.status_code == 403
    assert get_user_model().objects.filter(pk=non_admin.pk).exists()


@pytest.mark.django_db
def test_admin_generic_delete_on_other_user_is_unchanged(admin, other_non_admin):
    request = _authed("delete", f"/users/{other_non_admin.pk}/", admin)
    response = _view(request, pk=other_non_admin.pk)

    assert response.status_code == 204
    assert not get_user_model().objects.filter(pk=other_non_admin.pk).exists()


@pytest.mark.django_db
def test_admin_generic_patch_is_active_on_other_user_is_unchanged(admin, other_non_admin):
    request = _authed(
        "patch", f"/users/{other_non_admin.pk}/", admin, {"is_active": False}
    )
    response = _view(request, pk=other_non_admin.pk)

    assert response.status_code == 200
    other_non_admin.refresh_from_db()
    assert other_non_admin.is_active is False


@pytest.mark.django_db
def test_current_behaviour_is_unchanged(non_admin):
    view = _UserViewSet.as_view({"get": "current", "patch": "current"})

    request = _authed("get", "/users/current/", non_admin)
    response = view(request)
    assert response.status_code == 200
    assert response.data["id"] == non_admin.pk

    request = _authed("patch", "/users/current/", non_admin, {"first_name": "Via Current"})
    response = view(request)

    assert response.status_code == 200
    non_admin.refresh_from_db()
    assert non_admin.first_name == "Via Current"

    request = _authed("patch", "/users/current/", non_admin, {"is_active": False})
    response = view(request)

    assert response.status_code == 400


@pytest.mark.django_db
def test_non_admin_generic_patch_on_other_users_id_stays_404(non_admin, other_non_admin):
    # Regression guard: the allowlist check must run AFTER get_object(), not
    # before -- otherwise it fires unconditionally and turns the pre-existing
    # 404 (get_queryset() already excludes another user's row for a
    # non-admin) into a 400/403, for both disallowed and allowed fields.
    request = _authed(
        "patch", f"/users/{other_non_admin.pk}/", non_admin, {"is_active": False}
    )
    response = _view(request, pk=other_non_admin.pk)
    assert response.status_code == 404

    request = _authed(
        "patch", f"/users/{other_non_admin.pk}/", non_admin, {"first_name": "x"}
    )
    response = _view(request, pk=other_non_admin.pk)
    assert response.status_code == 404


@pytest.mark.django_db
def test_non_admin_generic_delete_on_other_users_id_stays_404(non_admin, other_non_admin):
    request = _authed("delete", f"/users/{other_non_admin.pk}/", non_admin)
    response = _view(request, pk=other_non_admin.pk)

    assert response.status_code == 404
    assert get_user_model().objects.filter(pk=other_non_admin.pk).exists()


@pytest.mark.django_db
def test_non_admin_generic_put_on_self_does_not_require_full_payload(non_admin):
    # A bare PUT is full-replace under DRF and would otherwise fail
    # required-field validation (or, with a different serializer, reset
    # omitted fields to defaults). update() forces partial=True for a
    # non-admin so PUT behaves like current(): only the submitted,
    # allowlisted field changes.
    request = _authed("put", f"/users/{non_admin.pk}/", non_admin, {"first_name": "Put Name"})
    response = _view(request, pk=non_admin.pk)

    assert response.status_code == 200
    non_admin.refresh_from_db()
    assert non_admin.first_name == "Put Name"
    assert non_admin.is_active is True


@pytest.mark.django_db
def test_non_admin_generic_put_is_active_on_self_is_rejected(non_admin):
    request = _authed("put", f"/users/{non_admin.pk}/", non_admin, {"is_active": False})
    response = _view(request, pk=non_admin.pk)

    assert response.status_code == 400
    non_admin.refresh_from_db()
    assert non_admin.is_active is True


@pytest.mark.django_db
def test_non_admin_widened_queryset_patch_on_other_user_is_unaffected(
    non_admin, other_non_admin
):
    request = _authed(
        "patch", f"/users/{other_non_admin.pk}/", non_admin, {"is_active": False}
    )
    response = _wide_view(request, pk=other_non_admin.pk)

    assert response.status_code == 200
    other_non_admin.refresh_from_db()
    assert other_non_admin.is_active is False


@pytest.mark.django_db
def test_non_admin_widened_queryset_delete_on_other_user_is_unaffected(
    non_admin, other_non_admin
):
    request = _authed("delete", f"/users/{other_non_admin.pk}/", non_admin)
    response = _wide_view(request, pk=other_non_admin.pk)

    assert response.status_code == 204
    assert not get_user_model().objects.filter(pk=other_non_admin.pk).exists()


@pytest.mark.django_db
def test_non_admin_widened_queryset_self_target_still_gated(non_admin):
    request = _authed("patch", f"/users/{non_admin.pk}/", non_admin, {"is_active": False})
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 400

    request = _authed(
        "patch", f"/users/{non_admin.pk}/", non_admin, {"first_name": "Wide Self"}
    )
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 200
    non_admin.refresh_from_db()
    assert non_admin.first_name == "Wide Self"

    request = _authed("put", f"/users/{non_admin.pk}/", non_admin, {"first_name": "Wide Put"})
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 200

    request = _authed("delete", f"/users/{non_admin.pk}/", non_admin)
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 403
    assert get_user_model().objects.filter(pk=non_admin.pk).exists()
