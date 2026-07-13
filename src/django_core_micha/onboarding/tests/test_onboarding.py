from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.onboarding.models import UNIVERSAL_STEP_KEYS, OnboardingStepConfig, get_step_config_map
from django_core_micha.onboarding.views import OnboardingStepConfigView


@pytest.mark.django_db
def test_step_config_map_creates_universal_and_extra_keys(settings):
    settings.ONBOARDING_EXTRA_STEP_KEYS = ["unread_messages", "browser_push", "custom_step"]

    config_map = get_step_config_map()

    assert set(config_map) == {*UNIVERSAL_STEP_KEYS, "unread_messages", "custom_step"}
    assert OnboardingStepConfig.objects.count() == 5


@pytest.mark.django_db
def test_authenticated_users_can_read_step_config():
    user = get_user_model().objects.create_user(username="reader", email="reader@example.test", password="password")
    request = APIRequestFactory().get("/onboarding/step-config/")
    force_authenticate(request, user=user)

    response = OnboardingStepConfigView.as_view()(request)

    assert response.status_code == 200
    assert {item["key"] for item in response.data} == set(UNIVERSAL_STEP_KEYS)


@pytest.mark.django_db
def test_patch_requires_admin_role(settings):
    settings.ROLE_DEFINITIONS = {"user": {"level": 1, "label": "User"}, "manager": {"level": 2, "label": "Manager"}}
    user = get_user_model().objects.create_user(username="member", email="member@example.test", password="password")
    user.profile = SimpleNamespace(role="user")
    request = APIRequestFactory().patch("/onboarding/step-config/", {"key": "browser_push", "enabled": False}, format="json")
    force_authenticate(request, user=user)

    response = OnboardingStepConfigView.as_view()(request)

    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_can_patch_and_unknown_key_is_rejected(settings):
    settings.ROLE_DEFINITIONS = {"user": {"level": 1, "label": "User"}, "manager": {"level": 2, "label": "Manager"}}
    user = get_user_model().objects.create_user(username="admin", email="admin@example.test", password="password")
    user.profile = SimpleNamespace(role="manager")
    factory = APIRequestFactory()

    update_request = factory.patch("/onboarding/step-config/", {"key": "browser_push", "enabled": False}, format="json")
    force_authenticate(update_request, user=user)
    update_response = OnboardingStepConfigView.as_view()(update_request)

    unknown_request = factory.patch("/onboarding/step-config/", {"key": "unknown", "enabled": True}, format="json")
    force_authenticate(unknown_request, user=user)
    unknown_response = OnboardingStepConfigView.as_view()(unknown_request)

    assert update_response.status_code == 200
    assert OnboardingStepConfig.objects.get(key="browser_push").enabled is False
    assert unknown_response.status_code == 400


@pytest.mark.django_db
def test_patch_rejects_non_boolean_enabled_value(settings):
    settings.ROLE_DEFINITIONS = {"manager": {"level": 2, "label": "Manager"}}
    user = get_user_model().objects.create_user(username="strict", email="strict@example.test", password="password")
    user.profile = SimpleNamespace(role="manager")
    request = APIRequestFactory().patch("/onboarding/step-config/", {"key": "browser_push", "enabled": "false"}, format="json")
    force_authenticate(request, user=user)

    response = OnboardingStepConfigView.as_view()(request)

    assert response.status_code == 400
