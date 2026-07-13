from django.contrib import admin

from .models import OnboardingStepConfig


@admin.register(OnboardingStepConfig)
class OnboardingStepConfigAdmin(admin.ModelAdmin):
    list_display = ("key", "enabled", "order")
    list_editable = ("enabled", "order")
