import json
from functools import wraps
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from .models import TermsAcceptance
from .services import TermsNotConfigured, TermsVersionMismatch, accept_current_terms, current_document


def _response(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _state(user, document):
    row = TermsAcceptance.objects.filter(user=user, app=document.app, version=document.version).first()
    return {"document": {"app": document.app, "version": document.version, "url": document.url},
            "accepted": row is not None, "accepted_at": row.accepted_at.isoformat() if row else None}


def _no_store(view):
    # outermost, so the CSRF 403 and the 405 of an unsupported method carry it too
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


@_no_store
@csrf_protect
@require_http_methods(["GET", "POST"])
def terms_view(request):
    if not request.user.is_authenticated:
        return _response({"detail": "authentication_required"}, 401)
    document = current_document()
    if document is None:
        return _response({"detail": "terms_not_configured"}, 404)
    if request.method == "GET":
        return _response(_state(request.user, document))
    try:
        data = json.loads(request.body or b"")
    except (ValueError, UnicodeDecodeError, TypeError):
        return _response({"detail": "invalid_request"}, 400)
    if not isinstance(data, dict):
        return _response({"detail": "invalid_request"}, 400)
    unexpected = sorted(set(data) - {"version", "accept"})
    if unexpected:
        return _response({"detail": "unexpected_fields", "fields": unexpected}, 400)
    if data.get("accept") is not True:
        return _response({"detail": "acceptance_required"}, 400)
    if not isinstance(data.get("version"), str) or not data["version"]:
        return _response({"detail": "version_required"}, 400)
    try:
        _, created = accept_current_terms(request.user, data["version"])
    except TermsNotConfigured:
        return _response({"detail": "terms_not_configured"}, 404)
    except TermsVersionMismatch as exc:
        return _response({"detail": "version_mismatch", **_state(request.user, exc.document)}, 409)
    return _response(_state(request.user, document), 201 if created else 200)
