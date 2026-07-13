import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.notifications.models import PushSubscription
from django_core_micha.notifications.views import PushSubscriptionView


ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


def post_subscription(user, endpoint, p256dh, auth, ua="test browser"):
    request = APIRequestFactory().post(
        "/api/notifications/preferences/push-subscription/",
        {
            "subscription": {
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
            },
            "ua": ua,
        },
        format="json",
    )
    force_authenticate(request, user=user)
    return PushSubscriptionView.as_view()(request)


@pytest.mark.django_db
def test_push_subscription_is_created_for_authenticated_user():
    user = get_user_model().objects.create_user(username="owner", email="owner@example.test", password="password")

    response = post_subscription(user, ENDPOINT, "initial-p256dh", "initial-auth")

    assert response.status_code == 201
    subscriptions = PushSubscription.objects.filter(endpoint=ENDPOINT)
    assert subscriptions.count() == 1
    assert subscriptions.get().user_id == user.id


@pytest.mark.django_db
def test_push_subscription_owner_repost_updates_existing_subscription():
    user = get_user_model().objects.create_user(username="owner", email="owner@example.test", password="password")
    assert post_subscription(user, ENDPOINT, "initial-p256dh", "initial-auth").status_code == 201

    response = post_subscription(user, ENDPOINT, "updated-p256dh", "updated-auth")

    assert response.status_code == 200
    subscriptions = PushSubscription.objects.filter(endpoint=ENDPOINT)
    assert subscriptions.count() == 1
    subscription = subscriptions.get()
    assert subscription.user_id == user.id
    assert subscription.p256dh == "updated-p256dh"
    assert subscription.auth == "updated-auth"


@pytest.mark.django_db
def test_push_subscription_cannot_be_hijacked_by_another_user():
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="owner", email="owner@example.test", password="password")
    other = user_model.objects.create_user(username="other", email="other@example.test", password="password")
    PushSubscription.objects.create(
        user=owner,
        endpoint=ENDPOINT,
        p256dh="owner-p256dh",
        auth="owner-auth",
    )

    response = post_subscription(other, ENDPOINT, "attempted-p256dh", "attempted-auth")

    assert response.status_code == 409
    subscription = PushSubscription.objects.get(endpoint=ENDPOINT)
    assert subscription.user_id == owner.id
    assert subscription.p256dh == "owner-p256dh"
    assert subscription.auth == "owner-auth"


@pytest.mark.django_db
def test_push_subscription_rejects_non_allowlisted_endpoint_before_creation():
    user = get_user_model().objects.create_user(username="ssrf", email="ssrf@example.test", password="password")
    endpoint = "https://169.254.169.254/x"

    response = post_subscription(user, endpoint, "p256dh", "auth")

    assert response.status_code == 400
    assert PushSubscription.objects.filter(endpoint=endpoint).count() == 0
