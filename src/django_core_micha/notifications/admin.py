from django.contrib import admin

from .models import NotificationPreference, PushSubscription


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "email_opt_in", "push_opt_in")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "endpoint", "created_at")
    search_fields = ("user__email", "endpoint")
