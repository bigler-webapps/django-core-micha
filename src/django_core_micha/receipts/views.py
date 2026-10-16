"""Session-authenticated JSON API; uploaded content and results are never stored."""
import json, logging
from functools import wraps
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from . import consent
from .errors import ReceiptError
from .prepare import MAX_FILE_BYTES
from .reader import read_receipts
log=logging.getLogger(__name__)

def _no_store(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        response=view(request,*args,**kwargs);response["Cache-Control"]="no-store";return response
    return wrapped

def _response(data,status=200):
    response=JsonResponse(data,status=status);response["Cache-Control"]="no-store";return response

@_no_store
@csrf_protect
@require_http_methods(["GET","POST","DELETE"])
def consent_view(request):
    if not request.user.is_authenticated:return _response({"code":"authentication_required"},401)
    if request.method=="POST":consent.grant(request.user)
    elif request.method=="DELETE":consent.revoke(request.user)
    return _response(consent.state(request.user))

@_no_store
@csrf_protect
@require_http_methods(["POST"])
def read_view(request):
    if not request.user.is_authenticated:return _response({"code":"authentication_required"},401)
    # Do not read or inspect the uploaded object before the standing consent check.
    if not consent.has_consent(request.user):return _response({"code":"consent_required"},403)
    try:declared=int(request.META.get("CONTENT_LENGTH") or "")
    except (TypeError,ValueError):return _response({"code":"invalid_request"},400)  # no usable length, no multipart parsing
    if declared>MAX_FILE_BYTES+(1<<20):return _response({"code":"file_too_large"},413)  # before the multipart body is parsed
    upload=request.FILES.get("file")
    if upload is None:return _response({"code":"invalid_request"},400)
    if upload.size>MAX_FILE_BYTES:return _response({"code":"file_too_large"},413)  # before the content is read into memory
    try:context=json.loads(request.POST.get("context","{}"))
    except (ValueError,TypeError):return _response({"code":"invalid_request"},400)
    if not isinstance(context,dict):return _response({"code":"invalid_request"},400)
    try:return _response({"receipts":read_receipts(request.user,upload.read(),context=context)})
    except ReceiptError as exc:return _response({"code":exc.code},exc.status)

