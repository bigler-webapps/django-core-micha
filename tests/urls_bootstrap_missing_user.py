from django.urls import include, path

from django_core_micha.api_urls import urlpatterns as api_urlpatterns


urlpatterns = [path("api/", include(api_urlpatterns))]
