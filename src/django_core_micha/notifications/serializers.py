from rest_framework import serializers

from .models import NotificationPreference, PushSubscription
from .validators import is_allowed_push_endpoint


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ["email_opt_in", "push_opt_in"]


class PushSubscriptionEndpointValidationMixin:
    def validate_endpoint(self, value):
        if not is_allowed_push_endpoint(value):
            raise serializers.ValidationError("Endpoint must use an HTTPS URL from a supported push service.")
        return value


class PushSubscriptionInputSerializer(PushSubscriptionEndpointValidationMixin, serializers.Serializer):
    endpoint = serializers.CharField()


class PushSubscriptionSerializer(PushSubscriptionEndpointValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = PushSubscription
        fields = ["id", "endpoint", "p256dh", "auth", "ua", "created_at"]
        read_only_fields = ["id", "created_at"]
