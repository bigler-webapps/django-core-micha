import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import clear_url_caches
from django_core_micha.bootstrap import (
    _PROVIDERS,
    register_bootstrap_provider,
    unregister_bootstrap_provider,
)
from django_core_micha.notifications.models import Notification, NotificationRecipient


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "100/day", "user": "10000/day"},
}


@pytest.fixture(autouse=True)
def bootstrap_environment(settings):
    settings.ROOT_URLCONF = "tests.urls_bootstrap"
    settings.MIDDLEWARE = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "allauth.account.middleware.AccountMiddleware",
    ]
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
    settings.REST_FRAMEWORK = REST_FRAMEWORK.copy()
    cache.clear()
    clear_url_caches()
    _PROVIDERS.clear()
    yield
    _PROVIDERS.clear()
    cache.clear()
    clear_url_caches()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="bootstrap-user", email="bootstrap@example.test", password="test-password"
    )


def make_recipient(user, suffix):
    notification = Notification.objects.create(
        notification_type="bootstrap_test",
        category="system",
        urgency="normal",
        content={"title": suffix},
        dedup_key=f"bootstrap-{suffix}",
    )
    return NotificationRecipient.objects.create(notification=notification, user=user)


@pytest.mark.django_db
def test_anonymous_shape_auth_methods_and_csrf_cookie(client):
    response = client.get("/api/bootstrap/")
    auth_methods = client.get("/api/auth-methods/")

    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "auth_methods": auth_methods.json(),
        "notifications": None,
        "extensions": {},
    }
    assert "csrftoken" in response.cookies
    assert response["Cache-Control"] == "no-store"
    assert "Cookie" in response["Vary"]


@pytest.mark.django_db
def test_anonymous_bootstrap_calls_no_part_but_auth_methods(client, monkeypatch):
    from django_core_micha.notifications.views import CanonicalInboxView, CanonicalUnreadCountView
    from tests.urls_bootstrap import UserViewSet

    called = []
    monkeypatch.setattr(UserViewSet, "current", lambda self, request: called.append("user"))
    monkeypatch.setattr(CanonicalInboxView, "get", lambda self, request: called.append("feed"))
    monkeypatch.setattr(CanonicalUnreadCountView, "get", lambda self, request: called.append("count"))

    response = client.get("/api/bootstrap/")

    assert response.status_code == 200 and response.json()["user"] is None
    assert called == []


@pytest.mark.django_db
def test_authenticated_payload_matches_all_existing_endpoints(client, user):
    make_recipient(user, "one")
    make_recipient(user, "two")
    assert client.login(username=user.username, password="test-password")

    expected = {
        "user": client.get("/api/users/current/").json(),
        "auth_methods": client.get("/api/auth-methods/").json(),
        "feed": client.get("/api/notifications/feed/").json(),
        "unread_count": client.get("/api/notifications/feed/unread-count/").json(),
    }
    response = client.get("/api/bootstrap/?ignored=yes")

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == expected["user"]
    assert body["auth_methods"] == expected["auth_methods"]
    assert body["notifications"] == {
        "feed": expected["feed"],
        "unread_count": expected["unread_count"],
    }
    assert body["notifications"]["feed"]["next"] is None
    assert body["extensions"] == {}
    assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
def test_inactive_user_current_refusal_produces_anonymous_bootstrap(client, user):
    assert client.login(username=user.username, password="test-password")
    user.is_active = False
    user.save(update_fields=["is_active"])

    current = client.get("/api/users/current/")
    response = client.get("/api/bootstrap/")

    assert current.status_code in (401, 403)
    assert response.status_code == 200
    assert response.json()["user"] is None
    assert response.json()["notifications"] is None
    assert response.json()["extensions"] == {}


@pytest.mark.django_db
def test_signed_in_session_refused_by_users_current_gets_the_anonymous_shape(client, user, monkeypatch):
    from rest_framework.exceptions import PermissionDenied
    from django_core_micha.notifications.views import CanonicalInboxView
    from tests.urls_bootstrap import UserViewSet

    def refuse(self, request):
        raise PermissionDenied()

    called = []
    monkeypatch.setattr(UserViewSet, "current", refuse)
    monkeypatch.setattr(CanonicalInboxView, "get", lambda self, request: called.append("feed"))
    register_bootstrap_provider("probe", lambda request: called.append("provider") or {})
    assert client.login(username=user.username, password="test-password")

    assert client.get("/api/users/current/").status_code == 403
    response = client.get("/api/bootstrap/")

    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "auth_methods": client.get("/api/auth-methods/").json(),
        "notifications": None,
        "extensions": {},
    }
    assert called == []


@pytest.mark.django_db
def test_parts_resolve_under_a_script_prefix(client, user):
    from django.test import override_settings

    assert client.login(username=user.username, password="test-password")
    with override_settings(FORCE_SCRIPT_NAME="/app"):
        response = client.get("/api/bootstrap/")

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == user.pk
    assert body["notifications"]["unread_count"] == {"count": 0}


@pytest.mark.django_db
def test_pending_mfa_session_without_authenticated_user_is_anonymous(client):
    session = client.session
    session["account_login"] = {"pending": True, "user_id": 999}
    session.save()

    current = client.get("/api/users/current/")
    response = client.get("/api/bootstrap/")

    assert current.status_code in (401, 403)
    assert response.status_code == 200
    assert response.json()["user"] is None
    assert response.json()["notifications"] is None


@pytest.mark.django_db
def test_app_auth_methods_override_is_resolved_by_url_name(client):
    from django.test import override_settings

    with override_settings(ROOT_URLCONF="tests.urls_bootstrap_override"):
        clear_url_caches()
        response = client.get("/api/bootstrap/")
    clear_url_caches()

    assert response.status_code == 200
    assert response.json()["auth_methods"] == {"override": True}


@pytest.mark.django_db
def test_providers_run_only_for_authenticated_user_and_isolate_failures(client, user, caplog):
    calls = []

    def good(request):
        calls.append(request.user.pk)
        return {"id": request.user.pk}

    def raising(_request):
        raise RuntimeError("sensitive provider message")

    def unserialisable(_request):
        return {"bad": object()}

    register_bootstrap_provider("good", good)
    register_bootstrap_provider("raising", raising)
    register_bootstrap_provider("unserialisable", unserialisable)

    anonymous = client.get("/api/bootstrap/")
    assert anonymous.json()["extensions"] == {}
    assert calls == []

    assert client.login(username=user.username, password="test-password")
    authenticated = client.get("/api/bootstrap/")
    assert authenticated.json()["extensions"] == {
        "good": {"id": user.pk}, "raising": None, "unserialisable": None
    }
    assert calls == [user.pk]
    assert "provider raising failed (RuntimeError)" in caplog.text
    assert "sensitive provider message" not in caplog.text


def test_provider_registration_is_idempotent_but_rejects_duplicates_and_bad_names():
    def provider(_request):
        return {}

    register_bootstrap_provider("valid_name2", provider)
    register_bootstrap_provider("valid_name2", provider)
    with pytest.raises(ValueError):
        register_bootstrap_provider("valid_name2", lambda _request: {})
    for name in ("Upper", "1bad", "bad-name", "a" * 41):
        with pytest.raises(ValueError):
            register_bootstrap_provider(name, provider)
    unregister_bootstrap_provider("valid_name2")


@pytest.mark.django_db
def test_missing_user_route_returns_unavailable(client):
    from django.test import override_settings

    with override_settings(ROOT_URLCONF="tests.urls_bootstrap_missing_user"):
        clear_url_caches()
        response = client.get("/api/bootstrap/")
    clear_url_caches()
    assert response.status_code == 501
    assert response.json() == {"detail": "bootstrap_unavailable"}


@pytest.mark.django_db
def test_post_is_rejected_with_405_and_cookie_headers(client):
    response = client.post("/api/bootstrap/")
    assert response.status_code == 405
    assert response["Cache-Control"] == "no-store"
    assert "csrftoken" in response.cookies


@pytest.mark.django_db
def test_user_part_throttle_is_forwarded_with_retry_after(client, user):
    from django.test import override_settings

    with override_settings(ROOT_URLCONF="tests.urls_bootstrap_throttled"):
        clear_url_caches()
        cache.clear()
        assert client.login(username=user.username, password="test-password")
        assert client.get("/api/users/current/").status_code == 200
        response = client.get("/api/bootstrap/")
    clear_url_caches()

    assert response.status_code == 429
    assert response.json() == {"detail": "bootstrap_part_failed", "part": "user"}
    assert response.get("Retry-After")


@pytest.mark.django_db
def test_bootstrap_query_count_is_bounded_and_does_not_scale_with_feed_size(client, user):
    assert client.login(username=user.username, password="test-password")
    counts = []
    for size in (3, 25):
        NotificationRecipient.objects.filter(user=user).delete()
        Notification.objects.filter(dedup_key__startswith="bootstrap-query-").delete()
        for index in range(size):
            make_recipient(user, f"query-{index}")
        with CaptureQueriesContext(connection) as queries:
            response = client.get("/api/bootstrap/")
        assert response.status_code == 200
        notifications = response.json()["notifications"]
        assert notifications["feed"]["count"] == size and len(notifications["feed"]["results"]) == min(size, 20)
        assert notifications["unread_count"] == {"count": size}
        counts.append(len(queries))

    assert counts[0] == counts[1]
    assert counts[0] <= 35
