"""Minimal URLconf for exercising the push-action endpoints over a real session."""
from django.urls import include, path

urlpatterns = [
    path("api/notifications/", include("django_core_micha.notifications.urls")),
]
