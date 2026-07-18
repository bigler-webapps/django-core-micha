from django.contrib import admin

from .models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    PushSubscription,
)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "email_opt_in", "push_opt_in")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "endpoint", "created_at")
    search_fields = ("user__email", "endpoint")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("notification_type", "category", "dedup_key", "created_at")
    search_fields = ("dedup_key", "notification_type")


@admin.register(NotificationRecipient)
class NotificationRecipientAdmin(admin.ModelAdmin):
    list_display = ("notification", "user", "seen_at", "dismissed_at", "done_at")


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ("recipient", "channel", "status", "sent_at")
