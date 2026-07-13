from django.urls import path

from .views import (
    NotificationInboxView,
    NotificationMarkReadView,
    NotificationPreferenceView,
    NotificationUnreadCountView,
    PushSubscriptionView,
    VapidPublicKeyView,
)


urlpatterns = [
    path("preferences/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    path("preferences/push-subscription/", PushSubscriptionView.as_view(), name="push-subscription"),
    path("preferences/vapid-public-key/", VapidPublicKeyView.as_view(), name="vapid-public-key"),
    path("inbox/", NotificationInboxView.as_view(), name="notification-inbox"),
    path("inbox/unread-count/", NotificationUnreadCountView.as_view(), name="notification-unread-count"),
    path("inbox/mark-read/", NotificationMarkReadView.as_view(), name="notification-mark-read"),
]
