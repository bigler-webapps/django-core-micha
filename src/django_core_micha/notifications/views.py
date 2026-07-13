from django.conf import settings
from django.utils import timezone
from rest_framework import generics, status, views
from rest_framework.response import Response

from .models import NotificationPreference, PushSubscription, get_notification_model
from .serializers import NotificationPreferenceSerializer, PushSubscriptionInputSerializer, PushSubscriptionSerializer


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
