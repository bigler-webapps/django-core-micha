"""Authenticated REST surface for the consumer-agnostic messaging domain."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from .models import (Conversation, ConversationParticipant, Message, MessagingScope,
                     Poll, resolve_messaging_app, MessagingTenantResolutionError)
from .policy import get_messaging_policy
from .serializers import MessageInputSerializer, PollInputSerializer, serialize_conversation, serialize_message
from .services import (MessagingPermissionDenied, add_reaction, archive_conversation,
                       close_poll, create_conversation, create_poll, edit_message, mark_read,
                       mark_thread_read, open_direct, read_status, remove_reaction,
                       send_message, set_preferences, unread_counts, update_conversation_config,
                       vote_poll)

CURSOR_SALT = "django_core_micha.messaging.cursor.v1"


def _cursor(value):
    return signing.dumps(value, salt=CURSOR_SALT, compress=True)


def _decode_cursor(value):
    if not value:
        return None
    try:
        result = signing.loads(value, salt=CURSOR_SALT)
        if not isinstance(result, dict) or set(result) != {"created_at", "id"}:
            raise ValueError
        return result
    except Exception as exc:
        raise ValidationError({"cursor": "Invalid cursor."}) from exc


def _limit(request):
    try:
        value = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"limit": "Must be an integer."}) from exc
    if not 1 <= value <= 100:
        raise ValidationError({"limit": "Must be between 1 and 100."})
    return value


def _page(request, queryset, serializer):
    limit, cursor = _limit(request), _decode_cursor(request.query_params.get("cursor"))
    if cursor:
        queryset = queryset.filter(Q(created_at__gt=cursor["created_at"]) | Q(created_at=cursor["created_at"], id__gt=cursor["id"]))
    rows = list(queryset[: limit + 1])
    next_cursor = None
    if len(rows) > limit:
        rows.pop()
        last = rows[-1]
        next_cursor = _cursor({"created_at": last.created_at.isoformat(), "id": str(last.id)})
    return Response({"results": [serializer(row) for row in rows], "next_cursor": next_cursor})


def _idempotency_request_id(request, values):
    """Make the HTTP retry key and optimistic client ID one stable identity."""
    header = request.headers.get("Idempotency-Key")
    supplied = values.get("client_request_id")
    if header:
        try:
            header_id = __import__("uuid").UUID(header)
        except (ValueError, TypeError) as exc:
            raise ValidationError({"Idempotency-Key": "Must be a UUID."}) from exc
        if supplied and supplied != header_id:
            raise ValidationError({"client_request_id": "Must match Idempotency-Key."})
        values["client_request_id"] = header_id
    return values


class MessagingView(APIView):
    permission_classes = [IsAuthenticated]
    def _viewer_conversation(self, request, conversation_id):
        conversation = get_object_or_404(Conversation.objects.select_related("app", "scope"), pk=conversation_id)
        # A denied view is deliberately indistinguishable from a missing object.
        if not get_messaging_policy(conversation.app.app_key).can_view_conversation(actor=request.user, conversation=conversation):
            raise NotFound()
        return conversation

    def _participant_conversation(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id)
        if not ConversationParticipant.objects.filter(conversation=conversation, user=request.user, removed_at__isnull=True).exists():
            raise NotFound()
        return conversation

    @staticmethod
    def _service(call):
        try:
            return call()
        except MessagingPermissionDenied as exc:
            raise PermissionDenied() from exc
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc


class ConversationListView(MessagingView):
    def get(self, request):
        qs = Conversation.objects.select_related("app", "scope").filter(participants__user=request.user, participants__removed_at__isnull=True)
        if request.query_params.get("include_archived") != "true":
            qs = qs.filter(participants__archived_at__isnull=True)
        for field in ("scope_kind", "content_type", "object_id"):
            if request.query_params.get(field):
                qs = qs.filter(**{f"scope__{field.replace('scope_', '')}": request.query_params[field]})
        rows = [c for c in qs.order_by("-last_message_at", "-created_at") if get_messaging_policy(c.app.app_key).can_view_conversation(actor=request.user, conversation=c)]
        # Conversation ordering differs from the chronological message cursor.
        return Response({"results": [serialize_conversation(c, c.participants.get(user=request.user)) for c in rows[:100]], "next_cursor": None})


class DirectConversationView(MessagingView):
    def post(self, request):
        target = get_object_or_404(get_user_model(), pk=request.data.get("target_user_id"))
        scope_id = request.data.get("scope")
        scope = get_object_or_404(MessagingScope.objects.select_related("app"), pk=scope_id) if scope_id else None
        try:
            app = resolve_messaging_app(scope=scope)
        except MessagingTenantResolutionError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if scope is not None and not ConversationParticipant.objects.filter(conversation__app=app, user=target).exists():
            raise ValidationError({"target_user_id": "Target user is not established in the resolved tenant."})
        # Global DMs have no User->MessagingApp relation in this schema.  The
        # singleton registry resolution above is the accepted v1 boundary.
        conversation = self._service(lambda: open_direct(actor=request.user, target=target, app=app, scope=scope))
        participant = ConversationParticipant.objects.get(conversation=conversation, user=request.user)
        return Response(serialize_conversation(conversation, participant), status=status.HTTP_201_CREATED)


class ConversationCreateView(MessagingView):
    kind = None
    def post(self, request):
        scope = get_object_or_404(MessagingScope.objects.select_related("app"), pk=request.data.get("scope"))
        participant_ids = request.data.get("participant_ids", [])
        if not isinstance(participant_ids, list): raise ValidationError({"participant_ids": "Must be a list."})
        users = list(get_user_model().objects.filter(pk__in=participant_ids))
        conversation = self._service(lambda: create_conversation(actor=request.user, app=scope.app, scope=scope, kind=self.kind, title=request.data.get("title"), participant_users=users, external_key=request.data.get("external_key")))
        return Response(serialize_conversation(conversation, ConversationParticipant.objects.get(conversation=conversation, user=request.user)), status=status.HTTP_201_CREATED)


class ConversationMessagesView(MessagingView):
    def get(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id)
        return _page(request, Message.objects.filter(conversation=conversation, reply_to__isnull=True).select_related("conversation__app", "sender").prefetch_related("attachments", "reactions"), serialize_message)
    def post(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id); data = MessageInputSerializer(data=request.data); data.is_valid(raise_exception=True)
        values = _idempotency_request_id(request, data.validated_data)
        reply = get_object_or_404(Message, pk=values.pop("reply_to")) if values.get("reply_to") else None
        message, created = self._service(lambda: send_message(actor=request.user, conversation=conversation, reply_to=reply, **values))
        return Response(serialize_message(message), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class MessageDetailView(MessagingView):
    def _message(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation__app", "sender").prefetch_related("attachments", "reactions"), pk=message_id)
        self._viewer_conversation(request, message.conversation_id); return message
    def get(self, request, message_id): return Response(serialize_message(self._message(request, message_id)))
    def patch(self, request, message_id):
        message = self._message(request, message_id)
        updated = self._service(lambda: edit_message(actor=request.user, message=message, body=request.data.get("body", message.body), title=request.data.get("title", message.title), link_target=request.data.get("link_target", message.link_target)))
        return Response(serialize_message(updated))
    def delete(self, request, message_id):
        from .services import soft_delete_message
        self._service(lambda: soft_delete_message(actor=request.user, message=self._message(request, message_id)))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReactionView(MessagingView):
    def post(self, request, message_id):
        message = get_object_or_404(Message, pk=message_id); self._participant_conversation(request, message.conversation_id)
        self._service(lambda: add_reaction(actor=request.user, message=message, emoji=request.data.get("emoji")))
        return Response(serialize_message(Message.objects.prefetch_related("reactions", "attachments").get(pk=message.pk)))
    def delete(self, request, message_id, emoji):
        message = get_object_or_404(Message, pk=message_id); self._participant_conversation(request, message.conversation_id)
        self._service(lambda: remove_reaction(actor=request.user, message=message, emoji=emoji)); return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationReadView(MessagingView):
    def post(self, request, conversation_id):
        conversation = self._participant_conversation(request, conversation_id); self._service(lambda: mark_read(actor=request.user, conversation=conversation)); return Response(status=status.HTTP_204_NO_CONTENT)


class ReadStatusView(MessagingView):
    def get(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation__app"), pk=message_id); self._viewer_conversation(request, message.conversation_id)
        return Response(self._service(lambda: read_status(actor=request.user, message=message)))


class ConversationArchiveView(MessagingView):
    def post(self, request, conversation_id):
        conversation = self._participant_conversation(request, conversation_id); self._service(lambda: archive_conversation(actor=request.user, conversation=conversation, archived=bool(request.data.get("archived", True)))); return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationPreferencesView(MessagingView):
    def post(self, request, conversation_id):
        conversation = self._participant_conversation(request, conversation_id); participant = self._service(lambda: set_preferences(actor=request.user, conversation=conversation, **{key: request.data[key] for key in ("muted", "email_enabled", "push_enabled") if key in request.data}))
        return Response({"muted": participant.muted, "email_enabled": participant.email_enabled, "push_enabled": participant.push_enabled})


class ConversationPollView(MessagingView):
    def post(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id)
        data = PollInputSerializer(data=request.data); data.is_valid(raise_exception=True)
        values = _idempotency_request_id(request, data.validated_data)
        poll, created = self._service(lambda: create_poll(actor=request.user, conversation=conversation, question=values["question"], options=values["options"], allow_multiple=values.get("allow_multiple", False), client_request_id=values.get("client_request_id")))
        return Response({"id": str(poll.id), "message_id": str(poll.message_id), "closed_at": poll.closed_at}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PollVoteView(MessagingView):
    def post(self, request, poll_id):
        poll = get_object_or_404(Poll.objects.select_related("message__conversation__app"), pk=poll_id)
        self._participant_conversation(request, poll.message.conversation_id)
        self._service(lambda: vote_poll(actor=request.user, poll=poll, option_ids=request.data.get("option_ids") or []))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PollCloseView(MessagingView):
    def post(self, request, poll_id):
        poll = get_object_or_404(Poll.objects.select_related("message__conversation__app"), pk=poll_id)
        self._participant_conversation(request, poll.message.conversation_id)
        poll = self._service(lambda: close_poll(actor=request.user, poll=poll))
        return Response({"id": str(poll.id), "closed_at": poll.closed_at})


class ThreadView(MessagingView):
    def get(self, request, root_id):
        root = get_object_or_404(Message.objects.select_related("conversation__app"), pk=root_id, reply_to__isnull=True)
        self._viewer_conversation(request, root.conversation_id)
        return _page(request, Message.objects.filter(reply_to=root).select_related("conversation__app", "sender").prefetch_related("attachments", "reactions"), serialize_message)


class ThreadReadView(MessagingView):
    def post(self, request, root_id):
        root = get_object_or_404(Message, pk=root_id, reply_to__isnull=True)
        self._participant_conversation(request, root.conversation_id)
        receipt = self._service(lambda: mark_thread_read(actor=request.user, root=root))
        return Response({"last_read_at": receipt.last_read_at})


class ConversationConfigView(MessagingView):
    def get(self, request, conversation_id):
        return Response(self._viewer_conversation(request, conversation_id).scope.config)

    def patch(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id)
        scope = self._service(lambda: update_conversation_config(actor=request.user, conversation=conversation, config=request.data.get("config", request.data)))
        return Response(scope.config)


class UnreadCountView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request): return Response(unread_counts(actor=request.user))
