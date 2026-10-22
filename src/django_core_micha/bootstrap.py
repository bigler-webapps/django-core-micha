"""Single-request bootstrap data assembled from the app's existing endpoints."""

from __future__ import annotations

import copy
import json
import logging
import re
from functools import wraps

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse, QueryDict
from django.urls import NoReverseMatch, Resolver404, get_script_prefix, resolve, reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


logger = logging.getLogger(__name__)
_PROVIDERS: dict[str, object] = {}
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")


def register_bootstrap_provider(name, provider) -> None:
    """Register a named app-specific provider. Repeated same-callable calls are safe."""
    if not isinstance(name, str) or not _PROVIDER_NAME.fullmatch(name):
        raise ValueError("Bootstrap provider names must match [a-z][a-z0-9_]{0,39}.")
    if not callable(provider):
        raise ValueError("Bootstrap providers must be callable.")
    existing = _PROVIDERS.get(name)
    if existing is not None and existing is not provider:
        raise ValueError(f"A bootstrap provider is already registered for {name!r}.")
    _PROVIDERS[name] = provider


def unregister_bootstrap_provider(name) -> None:
    """Test cleanup helper."""
    _PROVIDERS.pop(name, None)


def _with_response_headers(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        patch_vary_headers(response, ("Cookie",))
        return response

    return wrapped


def _part_path(url_name):
    """The part's full path (with the script prefix) and its path_info (without it)."""
    path = reverse(url_name)
    prefix = get_script_prefix()
    path_info = "/" + path[len(prefix):] if path.startswith(prefix) else path
    return path, path_info


def _part_response(request, url_name):
    path, path_info = _part_path(url_name)
    match = resolve(path_info)
    internal = copy.copy(request)
    internal.method = "GET"
    internal.path = path
    internal.path_info = path_info
    internal.META = request.META.copy()
    internal.META["REQUEST_METHOD"] = "GET"
    internal.META["PATH_INFO"] = path_info
    internal.META["QUERY_STRING"] = ""
    internal.GET = QueryDict("", encoding=request.encoding)
    response = match.func(internal, *match.args, **match.kwargs)
    status_code = getattr(response, "status_code", 200)
    if hasattr(response, "data"):
        payload = response.data
    else:
        try:
            payload = json.loads(response.content.decode(response.charset or "utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            payload = None
    return response, status_code, payload


def _failed_part(part, status_code, source_response):
    response = JsonResponse(
        {"detail": "bootstrap_part_failed", "part": part}, status=status_code
    )
    retry_after = getattr(source_response, "get", lambda *_: None)("Retry-After")
    if retry_after:
        response["Retry-After"] = retry_after
    return response


def _extensions(request):
    result = {}
    for name, provider in tuple(_PROVIDERS.items()):
        try:
            value = provider(request)
            # Validate using Django's encoder so bad values fail under their own key.
            json.dumps(value, cls=DjangoJSONEncoder)
            result[name] = value
        except Exception as exc:
            logger.error("Bootstrap provider %s failed (%s)", name, type(exc).__name__)
            result[name] = None
    return result


@_with_response_headers
@ensure_csrf_cookie
@require_GET
def bootstrap_view(request):
    names = (
        ("user", "user-current"),
        ("auth_methods", "auth-methods"),
        ("notifications.feed", "canonical-notification-feed"),
        ("notifications.unread_count", "canonical-notification-unread-count"),
    )
    try:
        # Check the full contract before invoking anything, so a missing route is
        # unavailable for every caller.
        for _, url_name in names:
            resolve(_part_path(url_name)[1])
        # An anonymous session (also an inactive account or a login waiting for its
        # second factor) gets nothing beyond auth-methods: no other part is called.
        if not request.user.is_authenticated:
            user_response, user_status, user_data = None, 403, None
        else:
            user_response, user_status, user_data = _part_response(request, "user-current")
    except (NoReverseMatch, Resolver404):
        return JsonResponse({"detail": "bootstrap_unavailable"}, status=501)
    if user_status in (401, 403):
        try:
            auth_response, auth_status, auth_data = _part_response(request, "auth-methods")
        except NoReverseMatch:
            return JsonResponse({"detail": "bootstrap_unavailable"}, status=501)
        if auth_status != 200:
            return _failed_part("auth_methods", auth_status, auth_response)
        return JsonResponse({
            "user": None,
            "auth_methods": auth_data,
            "notifications": None,
            "extensions": {},
        })
    if user_status != 200:
        return _failed_part("user", user_status, user_response)

    parts = {"user": user_data}
    for key, url_name in names[1:]:
        try:
            part_response, status_code, payload = _part_response(request, url_name)
        except NoReverseMatch:
            return JsonResponse({"detail": "bootstrap_unavailable"}, status=501)
        if status_code != 200:
            return _failed_part(key, status_code, part_response)
        parts[key] = payload

    return JsonResponse({
        "user": parts["user"],
        "auth_methods": parts["auth_methods"],
        "notifications": {
            "feed": parts["notifications.feed"],
            "unread_count": parts["notifications.unread_count"],
        },
        "extensions": _extensions(request),
    })
