from rest_framework import serializers

from .models import NotificationPreference, PushSubscription


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ["email_opt_in", "push_opt_in"]


class PushSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushSubscription
        fields = ["id", "endpoint", "p256dh", "auth", "ua", "created_at"]
        read_only_fields = ["id", "created_at"]
