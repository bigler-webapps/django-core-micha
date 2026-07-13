import pytest

from django_core_micha.notifications.validators import is_allowed_push_endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/example",
        "https://updates.push.services.mozilla.com/wpush/v2/example",
        "https://web.push.apple.com/QH1/example",
        "https://wns2-xx1.notify.windows.com/w/?token=example",
    ],
)
def test_allowed_push_service_endpoints_are_accepted(endpoint):
    assert is_allowed_push_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/example",
        "https://169.254.169.254/latest/meta-data/",
        "https://internal.example.com/",
        "https://localhost:8080/",
        "https://evilnotify.windows.com/",
        "https://notify.windows.com.attacker.com/",
    ],
)
def test_non_allowlisted_push_service_endpoints_are_rejected(endpoint):
    assert not is_allowed_push_endpoint(endpoint)
