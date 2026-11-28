"""Notification API views.

Canonical status-change WebSocket contract: ``{"type": "notification.status",
"notification_id": <id>, "status": {"seen": bool, "dismissed": bool,
"done": bool}}``. Consumers use this stable payload to synchronize projections.

NOTIF-13: every WS payload this app sends also carries ``"envelope":
"notification"`` (see ``delivery.notification_envelope``), additive alongside
the fields above. It lets ucm's Layer-1 realtime primitive
(``subscribe(envelope, handler)``) route this domain's messages without
misreading a second stream (e.g. messaging) as a notification. A payload with
no ``envelope`` field (pre-NOTIF-13 producers) is still treated as this
domain by that primitive's default, so older dcm releases remain compatible.
"""

from django.conf import settings
from django.core import signing
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.db import transaction
from django.db.models import Q, Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, status, views
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param, remove_query_param

from .delivery import notification_envelope, push_to_users
from .models import NotificationPreference, NotificationPushActionToken, NotificationRecipient, PushSubscription, get_notification_model
from .serializers import (
    CanonicalMarkInputSerializer,
    CanonicalNotificationSerializer,
    NotificationPreferenceSerializer,
    NotificationSubscriptionInputSerializer,
    PushSubscriptionInputSerializer,
    PushSubscriptionSerializer,
    PushDoneInputSerializer,
)
from .subscriptions import set_subscription
from .todo.registry import iter_registered_todo_types
from .todo.service import count_active_todos_for_user, derive_todos_for_user
from .types import iter_feed_hidden_type_keys


class NotificationPreferenceView(generics.RetrieveUpdateAPIView):
    serializer_class = NotificationPreferenceSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        preference, _ = NotificationPreference.objects.get_or_create(user=self.request.user)
        return preference


class NotificationSubscriptionView(views.APIView):
    """Toggle the current user's subscription to a discoverable category (NOTIF-26 scope D)."""

    def post(self, request):
        serializer = NotificationSubscriptionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = serializer.validated_data["category"]
        subscribed = serializer.validated_data["subscribed"]
        set_subscription(request.user, category, subscribed)
        return Response({"category": category, "subscribed": subscribed})


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


def _same_public_origin(origin):
    from urllib.parse import urlsplit

    if not origin:
        return False
    try:
        supplied = urlsplit(origin)
        expected = urlsplit(getattr(settings, "PUBLIC_ORIGIN", ""))
        return (
            supplied.scheme, supplied.hostname, supplied.port, supplied.path, supplied.query, supplied.fragment,
        ) == (
            expected.scheme, expected.hostname, expected.port, "", "", "",
        )
    except ValueError:
        return False


class PushCsrfView(views.APIView):
    """Issue a masked CSRF token only to the authenticated application origin."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not isinstance(request.successful_authenticator, SessionAuthentication):
            return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
        origin = request.headers.get("Origin")
        if origin and not _same_public_origin(origin):
            return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
        response = Response({"csrf_token": get_token(request._request)})
        response["Cache-Control"] = "no-store"
        return response


class PushDoneView(views.APIView):
    """Consume one recipient-bound done action without exposing token diagnostics."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, PermissionDenied):
            return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
        return super().handle_exception(exc)

    @staticmethod
    def _csrf_valid(request):
        # Invoke Django's normal origin/referer and masked-token validation, but return
        # the action endpoint's intentionally generic denial payload.
        return CsrfViewMiddleware(lambda _request: None).process_view(
            request._request, lambda _request: None, (), {}
        ) is None

    def post(self, request):
        if not isinstance(request.successful_authenticator, SessionAuthentication):
            return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
        if not _same_public_origin(request.headers.get("Origin")) or not self._csrf_valid(request):
            return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PushDoneInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
        from hashlib import sha256

        now = timezone.now()
        token_hash = sha256(serializer.validated_data["token"].encode("utf-8")).hexdigest()
        notification_id = None
        with transaction.atomic():
            token = (
                NotificationPushActionToken.objects.select_for_update()
                .select_related("recipient__notification")
                .filter(
                    token_hash=token_hash,
                    recipient__user=request.user,
                    action="done",
                    consumed_at__isnull=True,
                    expires_at__gt=now,
                )
                .first()
            )
            if token is None:
                return Response({"detail": "Denied."}, status=status.HTTP_403_FORBIDDEN)
            token.consumed_at = now
            token.save(update_fields=["consumed_at"])
            recipient = NotificationRecipient.objects.select_for_update().get(pk=token.recipient_id)
            notification_id = recipient.notification_id
            if recipient.done_at is None:
                recipient.done_at = now
                recipient.save(update_fields=["done_at"])
                transaction.on_commit(lambda: push_to_users(
                    [request.user], notification_envelope({
                        "type": "notification.status", "notification_id": notification_id,
                        "status": {"seen": recipient.seen_at is not None, "dismissed": recipient.dismissed_at is not None, "done": True},
                    })
                ))
        return Response(status=status.HTTP_204_NO_CONTENT)


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

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        self.keyset = "page" not in request.query_params
        if not self.keyset:
            return super().paginate_queryset(queryset, request, view)
        self.count = queryset.count() if hasattr(queryset, "model") else len(queryset)
        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                created, pk = signing.loads(cursor, salt="notification-feed")
                created = parse_datetime(created)
                if created is None or not isinstance(pk, int):
                    raise ValueError
            except (signing.BadSignature, ValueError, TypeError):
                raise NotFound("Invalid feed continuation.")
            if hasattr(queryset, "model"):
                queryset = queryset.filter(Q(notification__created_at__lt=created) | Q(notification__created_at=created, pk__lt=pk))
            else:
                queryset = [r for r in queryset if (r.notification.created_at, r.pk) < (created, pk)]
        rows = list(queryset[: self.page_size + 1])
        self.has_more = len(rows) > self.page_size
        self.rows = rows[: self.page_size]
        return self.rows

    def get_paginated_response(self, data):
        if not self.keyset:
            response = super().get_paginated_response(data)
            response.data["capabilities"] = {"mark_all_seen": True}
            return response
        next_link = None
        if self.has_more:
            last = self.rows[-1]
            cursor = signing.dumps([last.notification.created_at.isoformat(), last.pk], salt="notification-feed")
            next_link = replace_query_param(remove_query_param(self.request.build_absolute_uri(), "page"), "cursor", cursor)
        return Response({"count": self.count, "next": next_link, "previous": None, "results": data,
                         "capabilities": {"mark_all_seen": True}})


class CanonicalInboxView(generics.ListAPIView):
    """List the current user's canonical notification-recipient projection."""

    serializer_class = CanonicalNotificationSerializer
    pagination_class = CanonicalInboxPagination

    def get_queryset(self):
        event_recipients = (
            NotificationRecipient.objects.filter(user=self.request.user)
            .exclude(notification__category="todo")
            .exclude(notification__notification_type__in=iter_feed_hidden_type_keys())
            .select_related("notification", "notification__content_type")
            .order_by("-notification__created_at", "-pk")
        )
        status_filter = self.request.query_params.get("status")
        if status_filter == "unseen":
            event_recipients = event_recipients.filter(seen_at__isnull=True)
        elif status_filter == "active":
            event_recipients = event_recipients.filter(dismissed_at__isnull=True, done_at__isnull=True)
        elif status_filter == "done":
            event_recipients = event_recipients.filter(done_at__isnull=False)

        # No app currently registers a todo provider: skip the per-request provider
        # derive entirely and keep this queryset lazily DB-paginated (unpacking it
        # below would force fetching the user's entire notification history on every
        # request, regardless of page, which only pays off once todos actually exist).
        if not iter_registered_todo_types():
            return event_recipients

        # The full emitted set (not just active) so a "done"/unfiltered view has the
        # same dismissed/done parity with todos that it already has with event rows.
        live_todos = derive_todos_for_user(self.request.user)
        if status_filter == "unseen":
            live_todos = [recipient for recipient in live_todos if recipient.seen_at is None]
        elif status_filter == "active":
            live_todos = [
                recipient for recipient in live_todos
                if recipient.dismissed_at is None and recipient.done_at is None
            ]
        elif status_filter == "done":
            live_todos = [recipient for recipient in live_todos if recipient.done_at is not None]
        return sorted(
            [*event_recipients, *live_todos],
            key=lambda recipient: (recipient.notification.created_at, recipient.pk),
            reverse=True,
        )


class CanonicalUnreadCountView(views.APIView):
    """Return unread canonical rows, excluding dismissed items from the badge."""

    def get(self, request):
        # A dismissed-but-unseen item is no longer actionable and must not inflate a badge.
        event_count = NotificationRecipient.objects.filter(
            user=request.user,
            seen_at__isnull=True,
            dismissed_at__isnull=True,
            done_at__isnull=True,
        ).exclude(
            notification__category="todo"
        ).exclude(
            notification__notification_type__in=iter_feed_hidden_type_keys()
        ).count()
        todo_count = count_active_todos_for_user(request.user)
        return Response({"count": event_count + todo_count})


class CanonicalMarkAllSeenView(views.APIView):
    """Mark the current actionable projection at a bounded server-side boundary."""

    def post(self, request):
        if request.data != {}:
            return Response({"detail": "Expected an empty object."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            todos = derive_todos_for_user(request.user) if iter_registered_todo_types() else []
            todo_ids = [r.pk for r in todos if r.dismissed_at is None and r.done_at is None]
            boundary = NotificationRecipient.objects.aggregate(max_id=Max("pk"))["max_id"] or 0
            now = timezone.now()
            eligible = NotificationRecipient.objects.filter(
                user=request.user, pk__lte=boundary, seen_at__isnull=True,
                dismissed_at__isnull=True, done_at__isnull=True,
            ).filter(
                Q(pk__in=todo_ids) | (~Q(notification__category="todo") &
                    ~Q(notification__notification_type__in=iter_feed_hidden_type_keys()))
            )
            updated = eligible.update(seen_at=now)
            if updated:
                transaction.on_commit(lambda: push_to_users(
                    [request.user], notification_envelope({"type": "notification.inbox_changed"})
                ))
            unread_count = CanonicalUnreadCountView().get(request).data["count"]
        return Response({"updated": updated, "unread_count": unread_count})


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
                    notification_envelope({
                        "type": "notification.status",
                        "notification_id": recipient.notification_id,
                        "status": {
                            "seen": recipient.seen_at is not None,
                            "dismissed": recipient.dismissed_at is not None,
                            "done": recipient.done_at is not None,
                        },
                    }),
                )

        return Response({"updated": updated})


class ChannelMatrixView(views.APIView):
    """Read or update only the authenticated account's declared pairs."""

    from rest_framework.permissions import IsAuthenticated
    permission_classes = [IsAuthenticated]

    @staticmethod
    def row(user, category, channel, label_key):
        from .dispatch import _recipient_language
        from .models import NotificationCategoryChannelPreference
        from .prefs import is_channel_enabled
        from .text_registry import resolve_notification_text

        override = NotificationCategoryChannelPreference.objects.filter(
            user=user, category=category, channel=channel
        ).values_list("enabled", flat=True).first()
        return {
            "category": category, "channel": channel, "label_key": label_key,
            "label": resolve_notification_text(label_key, _recipient_language(user)) or label_key,
            "choice": "inherit" if override is None else ("enabled" if override else "disabled"),
            "effective_enabled": is_channel_enabled(user, category, channel),
        }

    def get(self, request):
        from .prefs import iter_editable_channel_pairs

        return Response({"results": [
            self.row(request.user, category, channel, label_key)
            for (category, channel), label_key in iter_editable_channel_pairs().items()
        ]})

    def patch(self, request):
        from .models import NotificationCategoryChannelPreference
        from .prefs import iter_editable_channel_pairs
        from .serializers import ChannelMatrixInputSerializer

        serializer = ChannelMatrixInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = serializer.validated_data["category"]
        channel = serializer.validated_data["channel"]
        choice = serializer.validated_data["choice"]
        if choice == "inherit":
            NotificationCategoryChannelPreference.objects.filter(
                user=request.user, category=category, channel=channel
            ).delete()
        else:
            NotificationCategoryChannelPreference.set_category_channel(
                request.user, category, channel, choice == "enabled"
            )
        return Response(self.row(request.user, category, channel, iter_editable_channel_pairs()[(category, channel)]))
