from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.throttling import UserRateThrottle

from django_core_micha.api_urls import urlpatterns as api_urlpatterns
from tests.urls_bootstrap import UserViewSet


class TinyUserRateThrottle(UserRateThrottle):
    THROTTLE_RATES = {"user": "1/day"}


class ThrottledUserViewSet(UserViewSet):
    throttle_classes = [TinyUserRateThrottle]


users_router = DefaultRouter()
users_router.register(r"", ThrottledUserViewSet, basename="user")

urlpatterns = [
    path("api/", include(api_urlpatterns)),
    path("api/users/", include(users_router.urls)),
]
