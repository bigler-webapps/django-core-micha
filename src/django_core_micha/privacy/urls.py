from django.urls import path

from .views import choices_view, manifest_view


urlpatterns = [
    path("manifest/", manifest_view, name="dcm-privacy-manifest"),
    path("choices/", choices_view, name="dcm-privacy-choices"),
]
