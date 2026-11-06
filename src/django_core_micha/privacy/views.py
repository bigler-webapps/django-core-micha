"""Anonymous-safe browser choice endpoints. Hosts mount these URLs explicitly."""

import hashlib
import json
import re
from functools import wraps

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import DatabaseError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods

from django_core_micha.auth.client_ip import resolve_client_ip

from .guard import RECEIPT_SALT, receipt_cookie_name, receipt_for_request
from .manifest import current_manifest, public_manifest_from_config
from .models import PrivacyChoice, PrivacyReceipt
from .services import apply_choice_request


MAX_BODY_BYTES = 4096
REQUEST_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.ASCII)


def _response(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _no_store(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


def _json_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        content_type = response.get("Content-Type", "")
        if response.status_code == 403 and not content_type.startswith("application/json"):
            return _response({"code": "csrf_failed", "retryable": False}, 403)
        if response.status_code == 405 and not content_type.startswith("application/json"):
            return _response({"code": "method_not_allowed", "retryable": False}, 405)
        return response
    return wrapped


def _origin_allowed(request, manifest):
    origin = request.headers.get("Origin")
    if not origin:
        return True
    if manifest["origins"] is not None:
        return origin in manifest["origins"]
    try:
        scheme = "https" if request.is_secure() else "http"
        return origin == f"{scheme}://{request.get_host()}"
    except (KeyError, ValueError):
        return False


def _origin_guard(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method == "POST":
            manifest = current_manifest()
            if manifest is not None and not _origin_allowed(request, manifest):
                return _response({"code": "origin_not_allowed", "retryable": False}, 403)
        return view(request, *args, **kwargs)
    return wrapped


def _method_json(methods):
    def decorate(view):
        return _json_errors(require_http_methods(methods)(view))
    return decorate


def _error(code, status, retryable=False, **extra):
    return _response({"code": code, "retryable": retryable, **extra}, status)


def _state(request, manifest):
    public = public_manifest_from_config(manifest)
    receipt = receipt_for_request(request)
    now = timezone.now()
    stored = {}
    if receipt is not None:
        stored = {
            choice.purpose_id: choice
            for choice in PrivacyChoice.objects.filter(receipt=receipt)
        }
    choices = {}
    for purpose in manifest["purposes"]:
        if purpose["required"]:
            continue
        choice = stored.get(purpose["id"])
        current = bool(
            choice
            and choice.purpose_revision == purpose["revision"]
            and choice.expires_at > now
        )
        choices[purpose["id"]] = {
            "state": choice.state if current else "unset",
            "revision": purpose["revision"],
            "expires_at": choice.expires_at.isoformat() if current else None,
        }
    return {
        "receipt_state": "present" if receipt is not None else "unset",
        "seq": receipt.seq if receipt is not None else 0,
        "config_version": public["config_version"],
        "notice_version": manifest["notice"]["version"],
        "choices": choices,
    }


def _decode_json_body(request):
    try:
        declared_length = request.META.get("CONTENT_LENGTH")
        if declared_length and int(declared_length) > MAX_BODY_BYTES:
            return None, _error("too_large", 413)
        # Bounded read: never buffer more than the cap, even for a chunked body without Content-Length.
        body = request.read(MAX_BODY_BYTES + 1)
    except (ValueError, TypeError, OSError):
        return None, _error("invalid_request", 400)
    if len(body) > MAX_BODY_BYTES:
        return None, _error("too_large", 413)
    if request.content_type != "application/json":
        return None, _error("invalid_request", 400)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeDecodeError, TypeError):
        return None, _error("invalid_request", 400)
    if not isinstance(data, dict):
        return None, _error("invalid_request", 400)
    return data, None


def _validate_choice_body(data, manifest):
    required_keys = {"request_id", "expected_seq", "notice_version", "config_version", "locale", "channel", "choices"}
    if set(data) != required_keys:
        return None, _error("invalid_request", 400)
    request_id = data["request_id"]
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        return None, _error("invalid_request", 400)
    expected_seq = data["expected_seq"]
    if type(expected_seq) is not int or expected_seq < 0:
        return None, _error("invalid_request", 400)
    if data["notice_version"] != manifest["notice"]["version"]:
        return None, _error("stale_notice", 409)
    # A client that rendered an older purpose description/revision must re-read before it can write.
    if data["config_version"] != public_manifest_from_config(manifest)["config_version"]:
        return None, _error("stale_config", 409)
    if data["locale"] not in manifest["locales"]:
        return None, _error("invalid_request", 400)
    if not isinstance(data["channel"], str) or data["channel"] not in {"banner", "settings", "other"}:
        return None, _error("invalid_request", 400)
    choices = data["choices"]
    if not isinstance(choices, dict) or not choices:
        return None, _error("invalid_request", 400)
    purposes = {purpose["id"]: purpose for purpose in manifest["purposes"]}
    normalized = {}
    for purpose_id, state in choices.items():
        purpose = purposes.get(purpose_id) if isinstance(purpose_id, str) else None
        if purpose is None or purpose["required"]:
            return None, _error("unknown_purpose", 422)
        if not isinstance(state, str) or state not in {PrivacyChoice.GRANTED, PrivacyChoice.REFUSED}:
            return None, _error("invalid_request", 400)
        normalized[purpose_id] = (purpose, state)
    return {
        "request_id": request_id,
        "expected_seq": expected_seq,
        "locale": data["locale"],
        "channel": data["channel"],
        "choices": normalized,
    }, None


def _throttle(bucket, identity, limit):
    digest = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()
    key = f"dcm-privacy:{bucket}:{digest}"
    if cache.add(key, 1, timeout=3600):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 2, timeout=3600)
        count = 2
    return count <= limit


def _origin_write_throttle(request):
    address = resolve_client_ip(request) or "unknown"
    if not _throttle("writes", address, 60):
        response = _error("throttled", 429, True)
        response["Retry-After"] = "3600"
        return response
    return None


def _set_receipt_cookie(response, manifest, receipt_id):
    value = signing.dumps(
        {"app": manifest["app"], "site": manifest["site"], "receipt_id": receipt_id},
        salt=RECEIPT_SALT,
    )
    response.set_cookie(
        receipt_cookie_name(manifest["site"]),
        value,
        max_age=manifest["choice_validity_days"] * 24 * 60 * 60,
        path="/",
        secure=bool(getattr(settings, "SESSION_COOKIE_SECURE", True)),
        httponly=True,
        samesite="Lax",
    )


@_no_store
@_method_json(["GET"])
def manifest_view(request):
    manifest = current_manifest()
    if manifest is None:
        return _error("not_configured", 404)
    return _response(public_manifest_from_config(manifest))


@csrf_exempt
@_no_store
@_json_errors
@_origin_guard
@csrf_protect
@require_http_methods(["GET", "POST"])
def choices_view(request):
    manifest = current_manifest()
    if manifest is None:
        return _error("not_configured", 404)
    if request.method == "GET":
        try:
            return _response(_state(request, manifest))
        except DatabaseError:
            return _error("unavailable", 503, True)

    try:
        throttled = _origin_write_throttle(request)
    except Exception:
        return _error("unavailable", 503, True)
    if throttled is not None:
        return throttled
    data, body_error = _decode_json_body(request)
    if body_error is not None:
        return body_error
    validated, validation_error = _validate_choice_body(data, manifest)
    if validation_error is not None:
        if validation_error.status_code == 409:
            try:
                stale_code = json.loads(validation_error.content)["code"]
                validation_error = _error(stale_code, 409, state=_state(request, manifest))
            except DatabaseError:
                validation_error = _error("unavailable", 503, True)
        return validation_error

    try:
        existing_receipt = receipt_for_request(request)
    except DatabaseError:
        return _error("unavailable", 503, True)
    if existing_receipt is None:
        address = resolve_client_ip(request) or "unknown"
        try:
            creation_allowed = _throttle("receipt-creation", address, 10)
        except Exception:
            return _error("unavailable", 503, True)
        if not creation_allowed:
            response = _error("throttled", 429, True)
            response["Retry-After"] = "3600"
            return response

    try:
        receipt_id, outcome = apply_choice_request(request, manifest, validated, existing_receipt)
    except DatabaseError:
        return _error("unavailable", 503, True)

    if outcome == "stale":
        try:
            return _error("stale", 409, state=_state(request, manifest))
        except DatabaseError:
            return _error("unavailable", 503, True)
    try:
        response = _response(_state(request, manifest))
    except DatabaseError:
        return _error("unavailable", 503, True)
    _set_receipt_cookie(response, manifest, receipt_id)
    return response
