"""NOTIF-26: reach axis (active/passive) + subscription-based recipient resolution.

Required tests 1, 2, 3, 5, 6, 7, 8 from work-orders/NOTIF-26.md. Test 4 (parity) lives
partly in messaging/tests/test_notifications.py (the messaging-type half) and partly in
cockpit's own suite (the six migrated status types). Test 9 (feed_visible
reconciliation) lives in test_canonical_notification_api.py, next to the feed view it
gates. Test 10 (ucm NotificationSettings) lives in ui-core-micha.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.notifications.api import notify, notify_subscribers
from django_core_micha.notifications.models import (
    Notification,
    NotificationCategorySubscription,
    NotificationDelivery,
    NotificationPreference,
)
from django_core_micha.notifications.router import resolve_channels
from django_core_micha.notifications.serializers import NotificationPreferenceSerializer
from django_core_micha.notifications.subscriptions import (
    is_subscribed,
    register_subscribable_category,
    resolve_category_subscribers,
    set_subscription,
)
from django_core_micha.notifications.text_registry import register_notification_text
from django_core_micha.notifications.types import (
    NotificationType,
    _REGISTRY,
    get_notification_type,
    register_notification_type,
)
from django_core_micha.notifications.views import NotificationPreferenceView


@pytest.fixture(autouse=True)
def isolated_registries():
    original_types = _REGISTRY.copy()
    from django_core_micha.notifications import subscriptions as subscriptions_module

    original_categories = subscriptions_module._SUBSCRIBABLE_CATEGORIES.copy()
    _REGISTRY.clear()
    subscriptions_module._SUBSCRIBABLE_CATEGORIES.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original_types)
    subscriptions_module._SUBSCRIBABLE_CATEGORIES.clear()
    subscriptions_module._SUBSCRIBABLE_CATEGORIES.update(original_categories)


def make_user(username, email=None):
    return get_user_model().objects.create_user(
        username=username, email=email if email is not None else f"{username}@example.test", password="password",
    )


def make_active_type(key="ops.active_only"):
    return NotificationType(key=key, category="ops", mode="event", resolution="user-done", active=True, passive=False)


def make_both_type(key="ops.both"):
    return NotificationType(key=key, category="ops", mode="event", resolution="user-done", active=True, passive=True)


def get_preferences(user):
    request = APIRequestFactory().get("/notifications/preferences/")
    force_authenticate(request, user=user)
    return NotificationPreferenceView.as_view()(request)


# --- Test 1: reach -> channel resolution per user-preference state --------------------


@pytest.mark.django_db
def test_active_type_resolves_to_chosen_channel_and_to_none_when_all_opted_out():
    ntype = make_active_type()
    subscribed_user = make_user("reach-opted-in")
    NotificationPreference.objects.create(user=subscribed_user, email_opt_in=True, push_opt_in=False)
    assert resolve_channels(ntype, subscribed_user) == ["email"]

    opted_out_user = make_user("reach-opted-out")
    NotificationPreference.objects.create(user=opted_out_user, email_opt_in=False, push_opt_in=False)
    assert resolve_channels(ntype, opted_out_user) == []


# --- Test 2: active-without-passive produces no chip and no feed entry ----------------


@pytest.mark.django_db
def test_active_without_passive_produces_no_chip_and_no_feed_entry():
    register_notification_type(make_active_type("jg-like.messaging"))
    user = make_user("active-only-recipient")
    NotificationPreference.objects.create(user=user, email_opt_in=True)

    notification = notify(
        type="jg-like.messaging", recipients=user, content={"title_key": "T", "body_key": "B"},
    )

    assert notification.recipients.count() == 1
    deliveries = set(
        NotificationDelivery.objects.filter(recipient__notification=notification).values_list("channel", flat=True)
    )
    assert "chip" not in deliveries
    assert deliveries == {"email"}
    ntype = get_notification_type("jg-like.messaging")
    assert ntype.feed_visible is False


# --- Test 3: bounded fallback (scope C), both halves together -------------------------


@pytest.mark.django_db
def test_bounded_fallback_both_type_degrades_to_passive_active_only_stays_undelivered():
    both_type = make_both_type("ops.both_fallback")
    active_only_type = make_active_type("ops.active_only_fallback")
    register_notification_type(both_type)
    register_notification_type(active_only_type)

    # No email, no push subscription, no preference row at all -- the "no usable
    # active channel" case from scope C.
    user = make_user("no-active-channel", email="")

    # Both-type: degrades to passive (chip resolves independently of active-channel
    # availability -- see router.resolve_channels docstring).
    assert resolve_channels(both_type, user) == ["chip"]

    # Active-only: nothing to degrade to -- stays undelivered.
    assert resolve_channels(active_only_type, user) == []

    # The settings surface must report the "no active channel configured" state
    # instead of silently vanishing (scope C).
    preference_row = NotificationPreference.objects.create(user=user)
    serializer = NotificationPreferenceSerializer(preference_row)
    types_by_key = {row["key"]: row for row in serializer.data["notification_types"]}
    assert types_by_key["ops.both_fallback"]["has_active_channel"] is False
    assert types_by_key["ops.active_only_fallback"]["has_active_channel"] is False


# --- Test 5: the default-True trap is closed for subscription-category events --------


@pytest.mark.django_db
def test_never_subscribed_user_receives_nothing_at_all_for_a_subscription_event():
    register_notification_type(make_both_type("ops.sweep_done"))
    subscriber = make_user("subscribed-ops-user")
    NotificationPreference.objects.create(user=subscriber, email_opt_in=True)
    bystander = make_user("never-touched-settings")
    set_subscription(subscriber, "ops", True)

    notification = notify_subscribers(
        type="ops.sweep_done", content={"title_key": "T", "body_key": "B"}, content_is_shareable=True,
    )

    assert notification is not None
    assert notification.recipients.count() == 1
    assert notification.recipients.filter(user=bystander).exists() is False
    # Not merely "no chip" -- the bystander is not a recipient at all, on any channel.
    assert not notification.recipients.filter(user=bystander).exists()


# --- Test 6: privacy constraint on subscription-category content ----------------------


@pytest.mark.django_db
def test_notify_subscribers_requires_explicit_content_shareable_acknowledgement():
    register_notification_type(make_both_type("ops.privacy_gate"))
    subscriber = make_user("privacy-subscriber")
    set_subscription(subscriber, "ops", True)

    with pytest.raises(ValueError):
        notify_subscribers(type="ops.privacy_gate", content={"title_key": "T", "body_key": "B"})

    assert Notification.objects.count() == 0

    notification = notify_subscribers(
        type="ops.privacy_gate", content={"title_key": "T", "body_key": "B"}, content_is_shareable=True,
    )
    assert notification is not None


# --- Test 7: empty subscriber list authors no Notification row ------------------------


@pytest.mark.django_db
def test_notify_subscribers_with_no_subscribers_authors_no_notification_row():
    register_notification_type(make_both_type("ops.nobody_subscribed"))

    result = notify_subscribers(
        type="ops.nobody_subscribed", content={"title_key": "T", "body_key": "B"}, content_is_shareable=True,
    )

    assert result is None
    assert Notification.objects.filter(notification_type="ops.nobody_subscribed").count() == 0


# --- Test 8: categories endpoint lists only this app's registered categories ---------


@pytest.mark.django_db
def test_preferences_endpoint_lists_only_registered_subscribable_categories():
    register_notification_text("NotificationCategory.ops.LABEL", {"de": "Betrieb", "en": "Operations", "fr": "Exploitation"})
    register_subscribable_category("ops", "NotificationCategory.ops.LABEL")
    user = make_user("categories-listing")
    NotificationPreference.objects.create(user=user)

    response = get_preferences(user)

    assert response.status_code == 200
    categories = response.data["subscribable_categories"]
    assert [row["category"] for row in categories] == ["ops"]
    # _recipient_language defaults to "de" for a user with no contact_profile.
    assert categories[0]["label"] == "Betrieb"
    assert categories[0]["subscribed"] is False

    set_subscription(user, "ops", True)
    response = get_preferences(user)
    assert response.data["subscribable_categories"][0]["subscribed"] is True


@pytest.mark.django_db
def test_notification_types_report_a_resolved_label_falling_back_to_category():
    register_notification_text("NotificationType.ops.labeled.LABEL", {"de": "Betriebs-Ereignis", "en": "Ops event"})
    labeled = NotificationType(
        key="ops.labeled", category="ops", mode="event", resolution="user-done",
        active=True, passive=False, label_key="NotificationType.ops.labeled.LABEL",
    )
    unlabeled = make_active_type("ops.unlabeled")
    register_notification_type(labeled)
    register_notification_type(unlabeled)
    user = make_user("label-fallback-user")
    NotificationPreference.objects.create(user=user, email_opt_in=True)

    rows = {
        row["key"]: row for row in NotificationPreferenceSerializer(
            NotificationPreference.objects.get(user=user)
        ).data["notification_types"]
    }

    # _recipient_language defaults to "de" for a user with no contact_profile.
    assert rows["ops.labeled"]["label"] == "Betriebs-Ereignis"
    # No label_key registered -> falls back to the raw category, never a blank/None.
    assert rows["ops.unlabeled"]["label"] == "ops"


@pytest.mark.django_db
def test_resolve_category_subscribers_ignores_channel_overrides_and_requires_explicit_opt_in():
    subscribed = make_user("resolver-subscribed")
    not_subscribed = make_user("resolver-not-subscribed")
    from django_core_micha.notifications.models import NotificationCategoryChannelPreference

    # A channel override for a category the user already receives must NOT make them
    # a subscriber -- the two consents are stored in separate models entirely.
    NotificationCategoryChannelPreference.objects.create(
        user=not_subscribed, category="ops", channel="chip", enabled=True,
    )
    NotificationCategorySubscription.objects.create(user=subscribed, category="ops")

    resolved = set(resolve_category_subscribers("ops").values_list("pk", flat=True))

    assert resolved == {subscribed.pk}
    assert is_subscribed(subscribed, "ops") is True
    assert is_subscribed(not_subscribed, "ops") is False
