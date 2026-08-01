"""Authenticated REST surface for the consumer-agnostic messaging domain."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from .models import (Conversation, ConversationParticipant, Message, MessagingScope,
                     MessageThreadReceipt, Poll, resolve_messaging_app, MessagingTenantResolutionError)
from .policy import get_messaging_policy
from .serializers import MessageInputSerializer, PollInputSerializer, serialize_conversation, serialize_message, serialize_poll
from .services import (MessagingPermissionDenied, add_reaction, archive_conversation,
                       close_poll, create_conversation, create_poll, edit_message, mark_read,
                       mark_thread_read, open_direct, read_status, remove_reaction,
                       send_message, set_preferences, unread_counts, update_conversation_config,
                       vote_poll)
from .models import MessageAttachment

CURSOR_SALT = "django_core_micha.messaging.cursor.v1"


def _poll_response(poll, user):
    poll = Poll.objects.select_related("message__conversation__app").prefetch_related("options__votes").get(pk=poll.pk)
    result = serialize_poll(poll)
    result["voted_option_ids"] = [str(option_id) for option_id in poll.options.filter(votes__user=user).values_list("id", flat=True)]
    return result


def _with_reply_count(queryset):
    """Free `reply_count`/`last_reply_at` on serialize_message for a list endpoint —
    a soft-deleted reply row still counts, so no filter narrows the `replies` join.
    An aggregate annotate() forces a GROUP BY, which silently drops Message.Meta's
    default ordering (QuerySet.ordered goes False — Django only auto-applies default
    ordering when query.group_by is None) — re-assert it explicitly or pagination
    cursors (and plain list order) become undefined, most visibly on Postgres where a
    GROUP BY with no ORDER BY has no guaranteed row order at all."""
    return queryset.annotate(reply_count=Count("replies", distinct=True), last_reply_at=Max("replies__created_at")).order_by("created_at", "id")


def _thread_receipt(message, user):
    """thread_last_read_at is viewer-specific and REST-only — never part of
    serialize_message's own output, or it would leak into the message/message_edited
    realtime frames the same way voted_option_ids must never leak into poll_updated."""
    return MessageThreadReceipt.objects.filter(root=message, user=user).values_list("last_read_at", flat=True).first()


def _message_response(message, user):
    data = serialize_message(message)
    data["thread_last_read_at"] = _thread_receipt(message, user)
    return data


def _message_page_response(request, queryset, user):
    """Cursor-paginated message list, with thread_last_read_at attached via exactly
    one bulk query for the whole page (not per row) — the N+1 case this WO guards
    against."""
    rows, next_cursor = _paginate_rows(request, queryset)
    receipts = dict(MessageThreadReceipt.objects.filter(user=user, root_id__in=[row.id for row in rows]).values_list("root_id", "last_read_at"))
    results = []
    for row in rows:
        data = serialize_message(row)
        data["thread_last_read_at"] = receipts.get(row.id)
        results.append(data)
    return Response({"results": results, "next_cursor": next_cursor})


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


def _paginate_rows(request, queryset):
    limit, cursor = _limit(request), _decode_cursor(request.query_params.get("cursor"))
    if cursor:
        queryset = queryset.filter(Q(created_at__gt=cursor["created_at"]) | Q(created_at=cursor["created_at"], id__gt=cursor["id"]))
    rows = list(queryset[: limit + 1])
    next_cursor = None
    if len(rows) > limit:
        rows.pop()
        last = rows[-1]
        next_cursor = _cursor({"created_at": last.created_at.isoformat(), "id": str(last.id)})
    return rows, next_cursor


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
        participant_filters = {"participants__user": request.user, "participants__removed_at__isnull": True}
        if request.query_params.get("include_archived") != "true":
            participant_filters["participants__archived_at__isnull"] = True
        qs = Conversation.objects.select_related("app", "scope").filter(**participant_filters)
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
        # Core resolves the tenant above; the app policy decides whether this
        # target may be addressed in it before open_direct creates any rows.
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
        queryset = _with_reply_count(Message.objects.filter(conversation=conversation, reply_to__isnull=True).select_related("conversation__app", "sender").prefetch_related("attachments", "reactions", "poll__options__votes"))
        return _message_page_response(request, queryset, request.user)
    def post(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id); data = MessageInputSerializer(data=request.data); data.is_valid(raise_exception=True)
        values = _idempotency_request_id(request, data.validated_data)
        reply = get_object_or_404(Message, pk=values.pop("reply_to")) if values.get("reply_to") else None
        message, created = self._service(lambda: send_message(actor=request.user, conversation=conversation, reply_to=reply, **values))
        return Response(_message_response(message, request.user), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ConversationAttachmentView(MessagingView):
    def post(self, request, conversation_id):
        conversation = self._viewer_conversation(request, conversation_id)
        files = request.FILES.getlist("files[]") or request.FILES.getlist("files")
        if not files: raise ValidationError({"files": "At least one file is required."})
        values = _idempotency_request_id(request, {"client_request_id": request.data.get("client_request_id")})
        reply = get_object_or_404(Message, pk=request.data["reply_to"]) if request.data.get("reply_to") else None
        # Keep message, files, notification and realtime callbacks in one outer
        # transaction; callbacks registered by send_message run only after every
        # attachment is durable, and are discarded on validation/scan failure.
        with transaction.atomic():
            message, created = self._service(lambda: send_message(actor=request.user, conversation=conversation, body=request.data.get("body"), reply_to=reply, client_request_id=values.get("client_request_id")))
            if created:
                from .attachments import create_attachment
                created_attachments = []
                try:
                    for order, upload in enumerate(files):
                        created_attachments.append(create_attachment(message=message, upload=upload, order=order))
                except Exception as exc:
                    from django.core.files.storage import default_storage
                    from .crypto import decrypt_text
                    for attachment in created_attachments:
                        for key in (attachment.blob_key, attachment.thumbnail_key):
                            if key:
                                default_storage.delete(decrypt_text(app_key=conversation.app.app_key, value=key))
                    # attachments.py raises django.core.exceptions.ValidationError for every
                    # rejection (bad MIME, oversize, mismatch, scan-hook denial). DRF's
                    # exception handler only special-cases Http404 and
                    # django.core.exceptions.PermissionDenied — an uncaught Django
                    # ValidationError propagates as an unhandled 500, not a 400. Translate
                    # it explicitly; anything else is a real bug and re-raises as-is.
                    if isinstance(exc, DjangoValidationError):
                        raise ValidationError({"files": exc.messages}) from exc
                    raise
        message = Message.objects.select_related("conversation__app", "sender").prefetch_related("attachments", "reactions", "poll__options__votes").get(pk=message.pk)
        return Response(_message_response(message, request.user), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class AttachmentDownloadView(MessagingView):
    thumbnail = False
    def get(self, request, attachment_id):
        thumbnail = self.thumbnail
        attachment = get_object_or_404(MessageAttachment.objects.select_related("message__conversation__app"), pk=attachment_id)
        self._viewer_conversation(request, attachment.message.conversation_id)
        from .attachments import attachment_bytes
        try: data = attachment_bytes(attachment, thumbnail=thumbnail)
        except FileNotFoundError: raise Http404
        response = FileResponse(__import__("io").BytesIO(data), as_attachment=True, filename="thumbnail.png" if thumbnail else "attachment")
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Type"] = "image/png" if thumbnail else attachment.content_type
        return response


class MessageDetailView(MessagingView):
    def _message(self, request, message_id):
        message = get_object_or_404(Message.objects.select_related("conversation__app", "sender").prefetch_related("attachments", "reactions", "poll__options__votes"), pk=message_id)
        self._viewer_conversation(request, message.conversation_id); return message
    def get(self, request, message_id): return Response(_message_response(self._message(request, message_id), request.user))
    def patch(self, request, message_id):
        message = self._message(request, message_id)
        updated = self._service(lambda: edit_message(actor=request.user, message=message, body=request.data.get("body", message.body), title=request.data.get("title", message.title), link_target=request.data.get("link_target", message.link_target)))
        return Response(_message_response(updated, request.user))
    def delete(self, request, message_id):
        from .services import soft_delete_message
        self._service(lambda: soft_delete_message(actor=request.user, message=self._message(request, message_id)))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReactionView(MessagingView):
    def post(self, request, message_id):
        message = get_object_or_404(Message, pk=message_id); self._participant_conversation(request, message.conversation_id)
        self._service(lambda: add_reaction(actor=request.user, message=message, emoji=request.data.get("emoji")))
        return Response(_message_response(Message.objects.prefetch_related("reactions", "attachments", "poll__options__votes").get(pk=message.pk), request.user))
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
        return Response(_poll_response(poll, request.user), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PollVoteView(MessagingView):
    def post(self, request, poll_id):
        poll = get_object_or_404(Poll.objects.select_related("message__conversation__app"), pk=poll_id)
        self._participant_conversation(request, poll.message.conversation_id)
        poll = self._service(lambda: vote_poll(actor=request.user, poll=poll, option_ids=request.data.get("option_ids") or []))
        return Response(_poll_response(poll, request.user))


class PollCloseView(MessagingView):
    def post(self, request, poll_id):
        poll = get_object_or_404(Poll.objects.select_related("message__conversation__app"), pk=poll_id)
        self._participant_conversation(request, poll.message.conversation_id)
        poll = self._service(lambda: close_poll(actor=request.user, poll=poll))
        return Response(_poll_response(poll, request.user))


class ThreadView(MessagingView):
    def get(self, request, root_id):
        root = get_object_or_404(Message.objects.select_related("conversation__app"), pk=root_id, reply_to__isnull=True)
        self._viewer_conversation(request, root.conversation_id)
        queryset = _with_reply_count(Message.objects.filter(reply_to=root).select_related("conversation__app", "sender").prefetch_related("attachments", "reactions", "poll__options__votes"))
        return _message_page_response(request, queryset, request.user)


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
