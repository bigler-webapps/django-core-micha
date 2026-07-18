import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
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
        MigrationExecutor(connection).migrate([(APP_LABEL, MIGRATION_0003)])
