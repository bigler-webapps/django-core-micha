from django.urls import path

from .views import (
    CanonicalInboxView,
    CanonicalMarkView,
    CanonicalUnreadCountView,
    NotificationInboxView,
    NotificationMarkReadView,
    NotificationPreferenceView,
    NotificationSubscriptionView,
    NotificationUnreadCountView,
    PushSubscriptionView,
    VapidPublicKeyView,
)


urlpatterns = [
    path("preferences/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    path("preferences/subscriptions/", NotificationSubscriptionView.as_view(), name="notification-subscriptions"),
    path("preferences/push-subscription/", PushSubscriptionView.as_view(), name="push-subscription"),
    path("preferences/vapid-public-key/", VapidPublicKeyView.as_view(), name="vapid-public-key"),
    path("inbox/", NotificationInboxView.as_view(), name="notification-inbox"),
    path("inbox/unread-count/", NotificationUnreadCountView.as_view(), name="notification-unread-count"),
    path("inbox/mark-read/", NotificationMarkReadView.as_view(), name="notification-mark-read"),
    path("feed/", CanonicalInboxView.as_view(), name="canonical-notification-feed"),
    path("feed/unread-count/", CanonicalUnreadCountView.as_view(), name="canonical-notification-unread-count"),
    path("feed/mark/", CanonicalMarkView.as_view(), name="canonical-notification-mark"),
]
