from django.urls import include, path


urlpatterns = [path("privacy/", include("django_core_micha.privacy.urls"))]
