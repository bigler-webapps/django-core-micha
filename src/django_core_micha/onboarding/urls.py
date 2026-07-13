from django.urls import path

from .views import OnboardingStepConfigView


urlpatterns = [
    path("step-config/", OnboardingStepConfigView.as_view(), name="onboarding-step-config"),
]
