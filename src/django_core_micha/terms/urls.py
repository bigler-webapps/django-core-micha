from django.urls import path
from .views import terms_view

urlpatterns = [path("", terms_view, name="dcm-terms")]
