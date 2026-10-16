from django.urls import path
from . import views

urlpatterns=[path("consent/",views.consent_view,name="receipts-consent"),path("read/",views.read_view,name="receipts-read")]
