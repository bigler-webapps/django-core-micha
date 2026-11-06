"""Server-side, fail-closed checks for current purpose grants."""

from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.utils import timezone

from .manifest import current_manifest
from .models import PrivacyChoice, PrivacyReceipt


RECEIPT_SALT = "dcm-privacy-receipt"


def receipt_cookie_name(site):
    return f"dcm_privacy_{site}"


def receipt_for_request(request):
    """Resolve the host-only browser receipt without revealing validation failures."""
    manifest = current_manifest()
    if manifest is None:
        return None
    cookie = getattr(request, "COOKIES", {}).get(receipt_cookie_name(manifest["site"]))
    if not cookie:
        return None
    try:
        payload = signing.loads(
            cookie,
            salt=RECEIPT_SALT,
            max_age=manifest["choice_validity_days"] * 24 * 60 * 60,
        )
    except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"app", "site", "receipt_id"}
        or payload.get("app") != manifest["app"]
        or payload.get("site") != manifest["site"]
        or not isinstance(payload.get("receipt_id"), str)
    ):
        return None
    try:
        return PrivacyReceipt.objects.get(
            receipt_id=payload["receipt_id"], app=manifest["app"], site=manifest["site"]
        )
    except PrivacyReceipt.DoesNotExist:
        return None


def has_grant(request, purpose_id, service_id=None):
    """True only for a current grant on a configured optional purpose.

    When ``service_id`` is given, the service must also be declared under that purpose in the
    manifest; an undeclared service never inherits the purpose's grant.
    """
    manifest = current_manifest()
    if manifest is None or not isinstance(purpose_id, str):
        return False
    purposes = {purpose["id"]: purpose for purpose in manifest["purposes"]}
    purpose = purposes.get(purpose_id)
    if purpose is None:
        return False
    if purpose["required"]:
        raise ImproperlyConfigured("Required privacy purposes are not checked as grants.")
    if service_id is not None and not any(
        service.get("id") == service_id for service in purpose.get("services", [])
    ):
        return False
    try:
        receipt = receipt_for_request(request)
        if receipt is None:
            return False
        return PrivacyChoice.objects.filter(
            receipt=receipt,
            purpose_id=purpose_id,
            purpose_revision=purpose["revision"],
            state=PrivacyChoice.GRANTED,
            expires_at__gt=timezone.now(),
        ).exists()
    except DatabaseError:
        return False


class PrivacyGrantRequired(Exception):
    code = "privacy_grant_required"


def require_grant(request, purpose_id, service_id=None):
    if not has_grant(request, purpose_id, service_id):
        raise PrivacyGrantRequired("privacy_grant_required")
