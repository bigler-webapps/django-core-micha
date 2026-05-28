# auth/exception_handler.py
import logging

from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, Throttled, ValidationError
from rest_framework.response import Response

logger = logging.getLogger(__name__)

AUTH_CODE_MAP = {
    "not_authenticated": "auth.not_authenticated",
    "authentication_failed": "auth.authentication_failed",
    "permission_denied": "auth.permission_denied",
}

_AUDIT_EVENT_TYPES = {
    NotAuthenticated: "drf.not_authenticated",
    PermissionDenied: "drf.permission_denied",
    Throttled: "drf.throttled",
}


def _is_error_object(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    return any(k in d for k in ("code", "i18nKey", "message", "params"))


def _flatten(detail, field=None):
    out = []

    if isinstance(detail, dict):
        # Treat as leaf error object (do NOT recurse)
        if _is_error_object(detail):
            out.append({
                "field": field,
                "code": str(detail.get("code", "error")),
                **({"message": str(detail.get("message"))} if detail.get("message") is not None else {}),
                **({"i18nKey": str(detail.get("i18nKey"))} if detail.get("i18nKey") is not None else {}),
                **({"params": detail.get("params")} if isinstance(detail.get("params"), dict) else {}),
            })
            return out

        # Default: recurse
        for k, v in detail.items():
            nested_field = f"{field}.{k}" if field else k
            out.extend(_flatten(v, nested_field))
        return out

    if isinstance(detail, list):
        for item in detail:
            out.extend(_flatten(item, field))
        return out

    # Leaf: ErrorDetail / string / etc.
    code = getattr(detail, "code", "error")
    out.append({
        "field": field,
        "code": str(code),
        "message": str(detail),
    })
    return out


def _log_authz_audit_event(exc, context) -> None:
    """S213 — persist PermissionDenied / NotAuthenticated / Throttled as AuditEvent."""
    event_type = next(
        (et for cls, et in _AUDIT_EVENT_TYPES.items() if isinstance(exc, cls)),
        None,
    )
    if event_type is None:
        return

    from django_core_micha.auditlog.models import AuditEvent

    request = context.get("request")
    view = context.get("view")
    actor = None
    if request and getattr(request, "user", None) and request.user.is_authenticated:
        actor = request.user

    metadata = {
        "view": view.__class__.__name__ if view else None,
        "action": getattr(view, "action", None),
        "method": request.method if request else None,
        "path": request.path if request else None,
        "detail": str(exc.detail) if hasattr(exc, "detail") else str(exc),
    }
    if isinstance(exc, Throttled) and exc.wait is not None:
        metadata["retry_after"] = int(exc.wait)

    try:
        AuditEvent.objects.create(
            actor=actor,
            event_type=event_type,
            metadata=metadata,
        )
    except Exception:
        # Audit write failure must never abort the response.
        logger.exception("AuditEvent creation failed in exception_handler for %s", event_type)


def custom_exception_handler(exc, context):
    resp = drf_exception_handler(exc, context)
    if resp is None:
        return None

    # S213 — log authz denials before any response mutation
    _log_authz_audit_event(exc, context)

    if isinstance(exc, ValidationError):
        return Response({"errors": _flatten(resp.data)}, status=resp.status_code)

    if resp.status_code in (401, 403):
        default_code = getattr(exc, "default_code", None) or (
            getattr(getattr(exc, "detail", None), "code", None)
        )
        if resp.status_code == 401 and not default_code:
            default_code = "not_authenticated"
        if resp.status_code == 403 and not default_code:
            default_code = "permission_denied"

        if isinstance(resp.data, dict):
            if default_code:
                resp.data.setdefault("code", str(default_code))
                resp.data.setdefault("i18nKey", AUTH_CODE_MAP.get(str(default_code)))
        else:
            resp.data = {
                "detail": resp.data,
                "code": str(default_code or "error"),
                "i18nKey": AUTH_CODE_MAP.get(str(default_code or "error")),
            }

    return resp
