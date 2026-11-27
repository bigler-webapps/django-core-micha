from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import can_view_chat, is_creator
from .group_grants import catalogue, projection, save_selection
from .hooks import _hook, is_shared_scope
from .models import AssistantChat


class GrantSelectionSerializer(serializers.Serializer):
    group_ids = serializers.ListField(child=serializers.RegexField(r"\A[A-Za-z0-9_-]{1,64}\Z"), max_length=100)
    grant_revision = serializers.CharField(max_length=64)
    catalogue_revision = serializers.CharField(max_length=64)
    reauthorize_group_ids = serializers.ListField(child=serializers.RegexField(r"\A[A-Za-z0-9_-]{1,64}\Z"), max_length=100, required=False, default=list)

    def validate(self, attrs):
        if set(self.initial_data) - {"group_ids", "grant_revision", "catalogue_revision", "reauthorize_group_ids"}:
            raise serializers.ValidationError("invalid_request")
        if len(set(attrs["group_ids"])) != len(attrs["group_ids"]):
            raise serializers.ValidationError("invalid_request")
        return attrs


def problem(code, status):
    return Response({"code": code, "error": code}, status=status)


def private_chat(user, chat_id, lock=False):
    qs = AssistantChat.objects.select_for_update() if lock else AssistantChat.objects
    chat = qs.filter(pk=chat_id).first()
    if chat is None or not can_view_chat(user, chat) or not is_creator(user, chat) or is_shared_scope(chat.scope):
        raise Http404
    return chat


class ChatGroupGrantsView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def get(self, request, chat_id):
        chat = private_chat(request.user, chat_id)
        if chat.archived_at:
            return problem("archived", 409)
        groups = catalogue(chat, request.user)
        return Response({"supported": False} if groups is None else projection(chat, groups))

    @transaction.atomic
    def put(self, request, chat_id):
        chat = private_chat(request.user, chat_id, lock=True)
        if chat.archived_at:
            return problem("archived", 409)
        groups = catalogue(chat, request.user)
        if groups is None:
            return problem("not_configured", 404)
        serializer = GrantSelectionSerializer(data=request.data)
        if not serializer.is_valid():
            return problem("invalid_request", 400)
        data = serializer.validated_data
        if data["grant_revision"] != str(chat.grant_revision):
            return problem("stale_grants", 409)
        try:
            save_selection(chat, groups, data["group_ids"], data["reauthorize_group_ids"])
        except ValueError:
            return problem("invalid_selection", 400)
        return Response(projection(chat, groups))
