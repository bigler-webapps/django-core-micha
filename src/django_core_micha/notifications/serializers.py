from rest_framework import serializers

from .dispatch import _recipient_language
from .models import NotificationPreference, PushSubscription
from .router import resolve_channels
from .subscriptions import is_subscribed, iter_subscribable_categories
from .text_registry import resolve_notification_text
from .types import iter_registered_event_types
from .validators import is_allowed_push_endpoint


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """NOTIF-26 scope G/H: also reports this app's registered event types (reach +
    per-user active-channel availability) and its subscribable categories, so
    ui-core-micha's ``NotificationSettings`` renders both data-driven, with no props.
    """

    notification_types = serializers.SerializerMethodField()
    subscribable_categories = serializers.SerializerMethodField()

    class Meta:
        model = NotificationPreference
        fields = [
            "email_opt_in", "push_opt_in", "push_preview_opt_in",
            "notification_types", "subscribable_categories",
        ]

    def get_notification_types(self, preference):
        user = preference.user
        language = _recipient_language(user)
        rows = []
        for key, ntype in iter_registered_event_types():
            has_active_channel = (
                bool(resolve_channels(ntype, user, override=["email", "push"])) if ntype.active else None
            )
            label = (resolve_notification_text(ntype.label_key, language) if ntype.label_key else None) or ntype.category
            rows.append({
                "key": key,
                "category": ntype.category,
                "label": label,
                "active": ntype.active,
                "passive": ntype.passive,
                "has_active_channel": has_active_channel,
            })
        return rows

    def get_subscribable_categories(self, preference):
        user = preference.user
        language = _recipient_language(user)
        return [
            {
                "category": category,
                "label": resolve_notification_text(label_key, language) or category,
                "subscribed": is_subscribed(user, category),
            }
            for category, label_key in iter_subscribable_categories().items()
        ]


class NotificationSubscriptionInputSerializer(serializers.Serializer):
    category = serializers.CharField()
    subscribed = serializers.BooleanField()

    def validate_category(self, value):
        if value not in iter_subscribable_categories():
            raise serializers.ValidationError("Unknown or non-subscribable category.")
        return value


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


class CanonicalNotificationSerializer(serializers.Serializer):
    """Read-only flattened canonical inbox representation."""

    id = serializers.IntegerField(read_only=True)
    notification_id = serializers.IntegerField(source="notification.pk", read_only=True)
    notification_type = serializers.CharField(source="notification.notification_type", read_only=True)
    category = serializers.CharField(source="notification.category", read_only=True)
    urgency = serializers.CharField(source="notification.urgency", read_only=True)
    content = serializers.JSONField(source="notification.content", read_only=True)
    notifiable = serializers.SerializerMethodField(read_only=True)
    created_at = serializers.DateTimeField(source="notification.created_at", read_only=True)
    seen_at = serializers.DateTimeField(read_only=True, allow_null=True)
    dismissed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    done_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def get_notifiable(self, recipient):
        """Expose target identity only; never dereference the generic relation."""

        content_type = recipient.notification.content_type
        label = f"{content_type.app_label}.{content_type.model}" if content_type is not None else None
        object_id = recipient.notification.object_id
        return {"content_type": label, "object_id": str(object_id) if object_id is not None else None}


class CanonicalMarkInputSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["seen", "dismissed", "done"])
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
