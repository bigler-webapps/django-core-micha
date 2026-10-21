from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import TermsAcceptance


@dataclass(frozen=True)
class TermsDocument:
    app: str
    version: str
    url: str


class TermsNotConfigured(Exception):
    pass


class TermsVersionMismatch(Exception):
    def __init__(self, document):
        self.document = document
        super().__init__("terms_version_mismatch")


def validate_document_setting(raw):
    if not isinstance(raw, dict) or set(raw) != {"app", "version", "url"}:
        return None, ["DCM_TERMS_DOCUMENT must contain exactly app, version and url."]
    app, version, url = raw.get("app"), raw.get("version"), raw.get("url")
    errors = []
    if not isinstance(app, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", app, flags=re.ASCII):
        errors.append("DCM_TERMS_DOCUMENT app is invalid.")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version, flags=re.ASCII):
        errors.append("DCM_TERMS_DOCUMENT version is invalid.")
    valid_url = False
    if isinstance(url, str) and len(url) <= 500:
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname)
        except ValueError:
            pass
    if not valid_url:
        errors.append("DCM_TERMS_DOCUMENT url must be an absolute HTTP(S) URL of at most 500 characters.")
    return (TermsDocument(app, version, url) if not errors else None), errors


def current_document():
    document, _ = validate_document_setting(getattr(settings, "DCM_TERMS_DOCUMENT", None))
    return document


def has_accepted_current_terms(user):
    document = current_document()
    return bool(document and getattr(user, "is_authenticated", False) and TermsAcceptance.objects.filter(
        user=user, app=document.app, version=document.version).exists())


def accept_current_terms(user, version):
    document = current_document()
    if document is None:
        raise TermsNotConfigured
    if version != document.version:
        raise TermsVersionMismatch(document)
    try:
        with transaction.atomic():
            row = TermsAcceptance.objects.create(user=user, app=document.app, version=document.version, document_url=document.url)
        return row, True
    except IntegrityError:
        return TermsAcceptance.objects.get(user=user, app=document.app, version=document.version), False
