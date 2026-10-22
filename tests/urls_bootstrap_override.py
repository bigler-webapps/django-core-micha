from django.http import JsonResponse
from django.urls import path

from tests.urls_bootstrap import urlpatterns as shared_urlpatterns


def overridden_auth_methods(request):
    return JsonResponse({"override": True})


urlpatterns = [
    path("api/auth-methods/", overridden_auth_methods, name="auth-methods"),
    *shared_urlpatterns,
]
