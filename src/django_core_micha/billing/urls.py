from django.urls import path
from . import views
urlpatterns=[path("me/",views.me,name="billing-me"),path("team/seats/",views.team_seats,name="billing-team-seats"),path("cost-report/",views.cost_report,name="billing-cost-report"),path("checkout/",views.checkout,name="billing-checkout"),path("portal/",views.portal,name="billing-portal"),path("webhook/",views.webhook,name="billing-webhook")]
