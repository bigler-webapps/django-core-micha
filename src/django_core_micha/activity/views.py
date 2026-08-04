from __future__ import annotations

import datetime

from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import serialize_query_response
from .services import (
    ActivityPermissionDenied,
    ActivityValidationError,
    query_activity,
    record_ping,
)


def _service(call):
    try:
        return call()
    except ActivityPermissionDenied as exc:
        # A denied read is deliberately indistinguishable from a scope dcm has
        # never heard of — do not confirm whether the scope exists.
        raise NotFound() from exc
    except ActivityValidationError as exc:
        raise ValidationError({"detail": str(exc)}) from exc


def _parse_anchor(raw):
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValidationError({"anchor": "Must be an ISO-8601 datetime."})
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime.timezone.utc)
    return parsed


class ActivityPingView(APIView):
    """Recording is always the actor's own presence — no policy check needed."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        app_key = request.data.get("app_key")
        content_type_label = request.data.get("content_type")
        object_id = request.data.get("object_id")
        if not app_key or not content_type_label or object_id in (None, ""):
            raise ValidationError({"detail": "app_key, content_type and object_id are required."})
        _service(
            lambda: record_ping(
                actor=request.user,
                app_key=app_key,
                content_type_label=content_type_label,
                object_id=object_id,
            )
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ActivityQueryView(APIView):
    """Reading exposes per-user presence — gated by the registered ActivityPolicy."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        app_key = request.query_params.get("app_key")
        content_type_label = request.query_params.get("content_type")
        object_id = request.query_params.get("object_id")
        range_key = request.query_params.get("range")
        if not app_key or not content_type_label or not object_id or not range_key:
            raise ValidationError(
                {"detail": "app_key, content_type, object_id and range are required."}
            )
        anchor = _parse_anchor(request.query_params.get("anchor"))
        rows, granularity = _service(
            lambda: query_activity(
                actor=request.user,
                app_key=app_key,
                content_type_label=content_type_label,
                object_id=object_id,
                range_key=range_key,
                anchor=anchor,
            )
        )
        return Response(serialize_query_response(rows, granularity))
