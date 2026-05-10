# django_core_micha/invitations/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import AccessCodeViewSet

router = DefaultRouter()
router.register(r"access-codes", AccessCodeViewSet, basename="access-code")

urlpatterns = [
    path("", include(router.urls)),
]
