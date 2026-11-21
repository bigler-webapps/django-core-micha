import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from django_core_micha.notifications.models import (
    NotificationCategoryChannelPreference,
    NotificationChannelDefault,
)
from django_core_micha.notifications.prefs import is_channel_enabled


APP_LABEL = "django_core_micha_notifications"
MIGRATION_0002 = "0002_notification_notificationrecipient_and_more"
MIGRATION_0003 = "0003_notification_channel_defaults"


def make_user(username):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="password",
    )


@pytest.mark.django_db
def test_category_override_beats_channel_default_and_helper_upserts():
    user = make_user("matrix-override")
    NotificationChannelDefault.set_channel_default(user, "email", False)
    NotificationCategoryChannelPreference.set_category_channel(user, "finance", "email", True)

    assert is_channel_enabled(user, "finance", "email") is True
    assert is_channel_enabled(user, "travel", "email") is False

    NotificationCategoryChannelPreference.set_category_channel(user, "finance", "email", False)
    NotificationChannelDefault.set_channel_default(user, "email", True)

    assert NotificationCategoryChannelPreference.objects.filter(user=user).count() == 1
    assert NotificationChannelDefault.objects.filter(user=user).count() == 1
    assert is_channel_enabled(user, "finance", "email") is False
    assert is_channel_enabled(user, "travel", "email") is True


@pytest.mark.django_db(transaction=True)
def test_0003_migration_preserves_legacy_channel_behavior_without_seeding():
    """0003 deliberately does NOT seed NotificationChannelDefault from existing preferences.

    Seeding once at migration time would freeze that snapshot ahead of any later change made
    through the still-live NotificationPreferenceView endpoint (tier 2 would then permanently
    outrank a fresher tier-3 legacy value). Instead, pre-existing users must keep resolving
    live through the NotificationPreference fallback with NO NotificationChannelDefault row
    ever auto-created for them.
    """

    target = [(APP_LABEL, MIGRATION_0002)]
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    old_apps = executor.loader.project_state(target).apps

    try:
        User = old_apps.get_model("auth", "User")
        NotificationPreference = old_apps.get_model(APP_LABEL, "NotificationPreference")
        user = User.objects.create(username="seed-matrix")
        NotificationPreference.objects.create(user_id=user.pk, email_opt_in=False, push_opt_in=True)

        executor = MigrationExecutor(connection)
        executor.migrate([(APP_LABEL, MIGRATION_0003)])

        current_user = django_apps.get_model("auth", "User").objects.get(pk=user.pk)
        assert is_channel_enabled(current_user, "any-category", "email") is False
        assert is_channel_enabled(current_user, "any-category", "push") is True
        assert is_channel_enabled(current_user, "any-category", "chip") is True
        assert not NotificationChannelDefault.objects.filter(user_id=user.pk).exists()

        # A later live toggle through the still-active preference path must take effect
        # immediately, precisely because no stale NotificationChannelDefault row shadows it.
        NotificationPreference_current = django_apps.get_model(APP_LABEL, "NotificationPreference")
        NotificationPreference_current.objects.filter(user_id=user.pk).update(email_opt_in=True)
        assert is_channel_enabled(current_user, "any-category", "email") is True
    finally:
        # Restore to the actual latest migration state, not a hardcoded target —
        # rolling back to a fixed "0003" here (as this test previously did) left
        # the test database's schema permanently stuck at 0003 for the rest of
        # the session once a later migration (0005, adding TodoOverride) shipped,
        # since `flush`-based test teardown never re-runs migrations on its own.
        # That silently broke every other test needing anything past 0003 whenever
        # this test happened to run first in the session.
        call_command("migrate", run_syncdb=True, verbosity=0)


@pytest.mark.django_db
def test_declared_matrix_updates_resets_and_tracks_live_fallback():
    from rest_framework.test import APIRequestFactory, force_authenticate

    from django_core_micha.notifications import prefs
    from django_core_micha.notifications.models import NotificationPreference
    from django_core_micha.notifications.text_registry import register_notification_text
    from django_core_micha.notifications.views import ChannelMatrixView

    class Client:
        """Calls the view directly (the test settings define no URL conf); ``user=None`` is anonymous."""

        user = None

        def _call(self, method, url, data=None):
            factory = APIRequestFactory()
            request = getattr(factory, method)(url, data, format="json") if data is not None else getattr(factory, method)(url)
            if self.user is not None:
                force_authenticate(request, user=self.user)
            response = ChannelMatrixView.as_view()(request)
            response.json = lambda: response.data
            return response

        def get(self, url):
            return self._call("get", url)

        def patch(self, url, data, format=None):
            return self._call("patch", url, data)

    original = prefs._EDITABLE_CHANNEL_PAIRS.copy()
    prefs._EDITABLE_CHANNEL_PAIRS.clear()
    try:
        user = make_user("matrix-api")
        other = make_user("matrix-other")
        client = Client()
        client.user = user
        url = "/notifications/preferences/channel-matrix/"
        assert client.get(url).json() == {"results": []}
        prefs.register_editable_channel_pair("messaging", "email", "matrix.email")
        prefs.register_editable_channel_pair("messaging", "push", "matrix.push")
        prefs.register_editable_channel_pair("todo", "email", "matrix.digest")
        register_notification_text("matrix.email", {"de": "Nachrichten"})
        NotificationPreference.objects.create(user=user, email_opt_in=False)
        NotificationCategoryChannelPreference.set_category_channel(other, "messaging", "email", True)
        rows = client.get(url).json()["results"]
        assert rows[0] == {"category": "messaging", "channel": "email", "label_key": "matrix.email",
                           "label": "Nachrichten", "choice": "inherit", "effective_enabled": False}
        payload = {"category": "messaging", "channel": "email", "choice": "enabled"}
        response = client.patch(url, payload, format="json")
        assert response.status_code == 200
        assert response.json()["choice"] == "enabled" and response.json()["effective_enabled"] is True
        assert prefs.is_channel_enabled(user, "todo", "email") is False
        assert prefs.is_digest_email_enabled(user) is False
        NotificationPreference.objects.filter(user=user).update(email_opt_in=True)
        payload["choice"] = "disabled"
        assert client.patch(url, payload, format="json").json()["effective_enabled"] is False
        payload["choice"] = "inherit"
        assert client.patch(url, payload, format="json").json()["effective_enabled"] is True
        assert not NotificationCategoryChannelPreference.objects.filter(user=user).exists()
        assert NotificationCategoryChannelPreference.objects.filter(user=other, enabled=True).exists()
        assert prefs.is_digest_email_enabled(user) is True
        for invalid in ({**payload, "user": other.pk}, {**payload, "category": "unknown"},
                        {**payload, "choice": "invalid"}, {**payload, "channel": "unknown"}):
            assert client.patch(url, invalid, format="json").status_code == 400
        client.user = None
        assert client.get(url).status_code in (401, 403)
    finally:
        prefs._EDITABLE_CHANNEL_PAIRS.clear()
        prefs._EDITABLE_CHANNEL_PAIRS.update(original)
