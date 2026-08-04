from django.urls import path

from .views import ActivityPingView, ActivityQueryView

urlpatterns = [
    path("ping/", ActivityPingView.as_view(), name="activity-ping"),
    path("query/", ActivityQueryView.as_view(), name="activity-query"),
]
