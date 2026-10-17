"""URL conf with the admin mounted at a non-default prefix (DCM-AUTH-7 post-hoc review)."""
from django.contrib.admin.sites import AdminSite
from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path("backend/admin/", AdminSite(name="backend").urls),
    path("api/", lambda request: HttpResponse("api")),
]
