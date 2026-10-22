from django.contrib.auth import get_user_model
from django.urls import include, path
from rest_framework import serializers
from rest_framework.routers import DefaultRouter

from django_core_micha.auth.views import BaseUserViewSet
from django_core_micha.api_urls import urlpatterns as api_urlpatterns


class MinimalUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "first_name", "last_name", "is_active"]


class UserViewSet(BaseUserViewSet):
    serializer_class = MinimalUserSerializer


users_router = DefaultRouter()
users_router.register(r"", UserViewSet, basename="user")

urlpatterns = [
    path("api/", include(api_urlpatterns)),
    path("api/users/", include(users_router.urls)),
]
