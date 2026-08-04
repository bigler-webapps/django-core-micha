import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from django_core_micha.activity.models import ActivityBucket
from django_core_micha.activity.policy import register_activity_policy, unregister_activity_policy
from tests.testapp.models import Widget

CONTENT_TYPE_LABEL = "testapp.widget"
APP_KEY = "test-app"


class AllowPolicy:
    def can_read_activity(self, **kwargs):
        return True


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="activity-view-user")


@pytest.fixture
def widget(db):
    return Widget.objects.create(name="Scope A")


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_ping_endpoint_creates_a_bucket(client, user, widget):
    response = client.post(
        "/activity/ping/",
        {"app_key": APP_KEY, "content_type": CONTENT_TYPE_LABEL, "object_id": str(widget.pk)},
        format="json",
    )
    assert response.status_code == 204
    assert ActivityBucket.objects.filter(user=user).count() == 1


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_query_endpoint_returns_404_when_no_policy_registered(client, widget):
    response = client.get(
        "/activity/query/",
        {"app_key": "never-registered-app", "content_type": CONTENT_TYPE_LABEL, "object_id": str(widget.pk), "range": "1d"},
    )
    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_query_endpoint_returns_rolled_up_buckets_when_permitted(client, user, widget):
    register_activity_policy(APP_KEY, AllowPolicy())
    try:
        client.post(
            "/activity/ping/",
            {"app_key": APP_KEY, "content_type": CONTENT_TYPE_LABEL, "object_id": str(widget.pk)},
            format="json",
        )
        response = client.get(
            "/activity/query/",
            {"app_key": APP_KEY, "content_type": CONTENT_TYPE_LABEL, "object_id": str(widget.pk), "range": "1d"},
        )
        assert response.status_code == 200
        assert response.data["granularity"] == "hour"
        assert len(response.data["buckets"]) == 1
    finally:
        unregister_activity_policy(APP_KEY)


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="django_core_micha.api_urls")
def test_ping_endpoint_requires_authentication():
    response = APIClient().post(
        "/activity/ping/",
        {"app_key": APP_KEY, "content_type": CONTENT_TYPE_LABEL, "object_id": "1"},
        format="json",
    )
    assert response.status_code == 401 or response.status_code == 403
