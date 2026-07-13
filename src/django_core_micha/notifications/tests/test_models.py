import pytest
from django.contrib.auth import get_user_model

from django_core_micha.notifications.models import (
    NotificationPreference,
    PushSubscription,
    get_notification_model,
)


@pytest.mark.django_db
def test_push_subscription_endpoint_is_unique():
    user = get_user_model().objects.create_user(username="one", email="one@example.test", password="password")
    PushSubscription.objects.create(user=user, endpoint="https://push.test/sub", p256dh="key", auth="auth")
    with pytest.raises(Exception):
        PushSubscription.objects.create(user=user, endpoint="https://push.test/sub", p256dh="key2", auth="auth2")


@pytest.mark.django_db
def test_notification_preference_defaults_are_opted_out():
    user = get_user_model().objects.create_user(username="prefs", email="prefs@example.test", password="password")
    preference = NotificationPreference.objects.create(user=user)
    assert preference.email_opt_in is False
    assert preference.push_opt_in is False


def test_get_notification_model_is_none_when_unset(settings):
    settings.NOTIFICATION_MODEL = ""
    assert get_notification_model() is None
