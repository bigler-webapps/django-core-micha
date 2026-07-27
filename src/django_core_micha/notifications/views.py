"""Notification API views.

Canonical status-change WebSocket contract: ``{"type": "notification.status",
"notification_id": <id>, "status": {"seen": bool, "dismissed": bool,
"done": bool}}``. Consumers use this stable payload to synchronize projections.
"""

from django.conf import settings
from django.utils import timezone
from rest_framework import generics, status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .delivery import push_to_users
from .models import NotificationPreference, NotificationRecipient, PushSubscription, get_notification_model
from .serializers import (
    CanonicalMarkInputSerializer,
    CanonicalNotificationSerializer,
    NotificationPreferenceSerializer,
    PushSubscriptionInputSerializer,
    PushSubscriptionSerializer,
)


class NotificationPreferenceView(generics.RetrieveUpdateAPIView):
    serializer_class = NotificationPreferenceSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        preference, _ = NotificationPreference.objects.get_or_create(user=self.request.user)
        return preference


class PushSubscriptionView(views.APIView):
    """List, upsert, or remove the current user's browser subscriptions."""

    def get(self, request):
        return Response(PushSubscriptionSerializer(request.user.push_subscriptions.all(), many=True).data)

    def post(self, request):
        subscription = request.data.get("subscription", request.data)
        if not isinstance(subscription, dict):
            return Response({"detail": "subscription must be an object."}, status=status.HTTP_400_BAD_REQUEST)
        keys = subscription.get("keys", {})
        endpoint = subscription.get("endpoint")
        p256dh = keys.get("p256dh") if isinstance(keys, dict) else subscription.get("p256dh")
        auth = keys.get("auth") if isinstance(keys, dict) else subscription.get("auth")
        if not all(isinstance(value, str) and value for value in (endpoint, p256dh, auth)):
            return Response({"detail": "endpoint, p256dh, and auth are required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = PushSubscriptionInputSerializer(data={"endpoint": endpoint})
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        endpoint = validated_data["endpoint"]
        push_subscription = PushSubscription.objects.filter(endpoint=endpoint).first()
        if push_subscription is not None and push_subscription.user_id != request.user.id:
            return Response(
                {"detail": "This push subscription belongs to another user."},
                status=status.HTTP_409_CONFLICT,
            )
        if push_subscription is None:
            push_subscription = PushSubscription(user=request.user, endpoint=endpoint)
            response_status = status.HTTP_201_CREATED
        else:
            response_status = status.HTTP_200_OK
        push_subscription.p256dh = p256dh
        push_subscription.auth = auth
        push_subscription.ua = request.data.get("ua", "")
        push_subscription.save()
        return Response(PushSubscriptionSerializer(push_subscription).data, status=response_status)

    def delete(self, request):
        subscription_id = request.data.get("id")
        endpoint = request.data.get("endpoint")
        subscriptions = request.user.push_subscriptions.all()
        if subscription_id is not None:
            subscriptions = subscriptions.filter(pk=subscription_id)
        elif endpoint:
            subscriptions = subscriptions.filter(endpoint=endpoint)
        else:
            return Response({"detail": "id or endpoint is required."}, status=status.HTTP_400_BAD_REQUEST)
        subscriptions.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VapidPublicKeyView(views.APIView):
    def get(self, request):
        return Response({"vapidPublicKey": getattr(settings, "VAPID_PUBLIC_KEY", "")})


class _OptionalInboxView(views.APIView):
    """Inbox endpoints return 501 until a project configures NOTIFICATION_MODEL."""

    def get_notification_model(self):
        return get_notification_model()

    def unavailable(self):
        return Response(
            {"detail": "NOTIFICATION_MODEL is not configured for this project."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class NotificationInboxView(_OptionalInboxView):
    def get(self, request):
        model = self.get_notification_model()
        if model is None:
            return self.unavailable()
        notifications = model.objects.filter(user=request.user).order_by("-created_at")
        fields = ["id", "level", "title", "body", "source", "url", "created_at", "read_at"]
        return Response(list(notifications.values(*fields)))


class NotificationUnreadCountView(_OptionalInboxView):
    def get(self, request):
        model = self.get_notification_model()
        if model is None:
            return self.unavailable()
        return Response({"count": model.objects.filter(user=request.user, read_at__isnull=True).count()})


class NotificationMarkReadView(_OptionalInboxView):
    def post(self, request):
        model = self.get_notification_model()
        if model is None:
            return self.unavailable()
        notification_ids = request.data.get("ids")
        queryset = model.objects.filter(user=request.user, read_at__isnull=True)
        if notification_ids is not None:
            if not isinstance(notification_ids, list):
                return Response({"detail": "ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(pk__in=notification_ids)
        return Response({"updated": queryset.update(read_at=timezone.now())})


class CanonicalInboxPagination(PageNumberPagination):
    page_size = 20


class CanonicalInboxView(generics.ListAPIView):
    """List the current user's canonical notification-recipient projection."""

    serializer_class = CanonicalNotificationSerializer
    pagination_class = CanonicalInboxPagination

    def get_queryset(self):
        queryset = (
            NotificationRecipient.objects.filter(user=self.request.user)
            .select_related("notification", "notification__content_type")
            .order_by("-notification__created_at")
        )
        status_filter = self.request.query_params.get("status")
        if status_filter == "unseen":
            return queryset.filter(seen_at__isnull=True)
        if status_filter == "active":
            return queryset.filter(dismissed_at__isnull=True, done_at__isnull=True)
        if status_filter == "done":
            return queryset.filter(done_at__isnull=False)
        return queryset


class CanonicalUnreadCountView(views.APIView):
    """Return unread canonical rows, excluding dismissed items from the badge."""

    def get(self, request):
        # A dismissed-but-unseen item is no longer actionable and must not inflate a badge.
        count = NotificationRecipient.objects.filter(
            user=request.user,
            seen_at__isnull=True,
            dismissed_at__isnull=True,
        ).count()
        return Response({"count": count})


class CanonicalMarkView(views.APIView):
    """Mark only the current user's canonical recipient rows and broadcast their status."""

    timestamp_fields = {
        "seen": "seen_at",
        "dismissed": "dismissed_at",
        "done": "done_at",
    }

    def post(self, request):
        serializer = CanonicalMarkInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        recipient_ids = serializer.validated_data["ids"]
        recipients = NotificationRecipient.objects.filter(user=request.user, pk__in=recipient_ids)
        matched_ids = list(recipients.values_list("pk", flat=True))
        updated = recipients.update(**{self.timestamp_fields[action]: timezone.now()})

        if updated:
            affected_recipients = (
                NotificationRecipient.objects.filter(user=request.user, pk__in=matched_ids)
                .select_related("notification")
                .order_by("pk")
            )
            sent_notification_ids = set()
            for recipient in affected_recipients:
                if recipient.notification_id in sent_notification_ids:
                    continue
                sent_notification_ids.add(recipient.notification_id)
                push_to_users(
                    [request.user],
                    {
                        "type": "notification.status",
                        "notification_id": recipient.notification_id,
                        "status": {
                            "seen": recipient.seen_at is not None,
                            "dismissed": recipient.dismissed_at is not None,
                            "done": recipient.done_at is not None,
                        },
                    },
                )

        return Response({"updated": updated})
