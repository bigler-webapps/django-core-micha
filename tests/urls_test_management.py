from django.urls import include, path


urlpatterns = [
    path("api/test-management/", include("django_core_micha.test_management.urls")),
]
