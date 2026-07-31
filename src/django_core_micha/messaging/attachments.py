"""Validated, encrypted attachment storage; deliberately no public media URLs."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _

from django_core_micha.validators.upload import detect_mime, sanitize_filename, validate_upload

from .crypto import decrypt_bytes, decrypt_text, encrypt_bytes
from .models import MessageAttachment

MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024
ALLOWED_MIMES = frozenset({
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation", "image/png", "image/jpeg", "image/gif", "image/webp",
})
IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


@dataclass(frozen=True)
class ScanResult:
    state: str = MessageAttachment.ScanState.UNSCANNED
    metadata: dict = field(default_factory=dict)


class MessagingScanHook(Protocol):
    def scan(self, *, app_key: str, attachment_id, plaintext_path: str, declared_type: str) -> ScanResult: ...


def _scan_hook():
    hook = getattr(settings, "MESSAGING_SCAN_HOOK", None)
    if isinstance(hook, str):
        from django.utils.module_loading import import_string
        hook = import_string(hook)
    return hook


def _read(upload):
    upload.seek(0)
    return upload.read()


def _safe_image(raw, detected):
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - deployment configuration failure
        raise ValidationError(_("Image processing is unavailable; image upload rejected.")) from exc
    try:
        with Image.open(BytesIO(raw)) as image:
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image)
            image.load()
            width, height = image.size
            if detected == "image/jpeg":
                image = image.convert("RGB")
                fmt, extension = "JPEG", "jpg"
            elif detected == "image/png":
                fmt, extension = "PNG", "png"
            elif detected == "image/webp":
                fmt, extension = "WEBP", "webp"
            else:
                # Re-encoding GIF removes metadata and animation safely.
                image = image.convert("RGBA")
                fmt, extension = "PNG", "png"
                detected = "image/png"
            output = BytesIO(); image.save(output, format=fmt); payload = output.getvalue()
            thumb = image.copy(); thumb.thumbnail((320, 320))
            thumb_out = BytesIO(); thumb.save(thumb_out, format="PNG")
            return payload, thumb_out.getvalue(), detected, width, height, extension
    except Exception as exc:
        raise ValidationError(_("Invalid image content.")) from exc


def create_attachment(*, message, upload, order):
    """Validate, optionally scan, encrypt and persist one uploaded file."""
    validate_upload(upload, allowed_mimes=ALLOWED_MIMES, max_size=MAX_ATTACHMENT_SIZE)
    detected = detect_mime(upload)
    declared = getattr(upload, "content_type", None)
    if declared and declared != detected:
        raise ValidationError(_("Declared file type does not match file content."))
    raw = _read(upload)
    thumbnail = None; width = height = None
    if detected in IMAGE_MIMES:
        raw, thumbnail, detected, width, height, extension = _safe_image(raw, detected)
    else:
        extension = os.path.splitext(str(getattr(upload, "name", "")))[1].lstrip(".")
    attachment_id = uuid.uuid4()
    scan_state = MessageAttachment.ScanState.UNSCANNED; scan_metadata = {}
    hook = _scan_hook()
    if hook:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(raw); plaintext_path = tmp.name
        try:
            result = hook.scan(app_key=message.conversation.app.app_key, attachment_id=attachment_id, plaintext_path=plaintext_path, declared_type=detected)
        finally:
            os.unlink(plaintext_path)
        if isinstance(result, dict): result = ScanResult(**result)
        if not isinstance(result, ScanResult): raise ValidationError(_("Messaging scan hook returned an invalid result."))
        scan_state, scan_metadata = result.state, result.metadata
        if scan_state == MessageAttachment.ScanState.REJECTED:
            raise ValidationError(_("Attachment rejected by the configured scanner."))
    app_key = message.conversation.app.app_key
    blob_name = f"messaging/{app_key}/{attachment_id}/blob"
    thumb_name = f"messaging/{app_key}/{attachment_id}/thumbnail" if thumbnail else None
    default_storage.save(blob_name, ContentFile(encrypt_bytes(app_key=app_key, value=raw)))
    try:
        if thumbnail: default_storage.save(thumb_name, ContentFile(encrypt_bytes(app_key=app_key, value=thumbnail)))
        return MessageAttachment.objects.create(id=attachment_id, message=message, blob_key=blob_name, filename=sanitize_filename(getattr(upload, "name", "file")), content_type=detected, byte_size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), order=order, width=width, height=height, thumbnail_key=thumb_name, scan_state=scan_state, scan_metadata=scan_metadata)
    except Exception:
        default_storage.delete(blob_name)
        if thumb_name: default_storage.delete(thumb_name)
        raise


def attachment_bytes(attachment, *, thumbnail=False):
    key = attachment.thumbnail_key if thumbnail else attachment.blob_key
    if not key: raise FileNotFoundError
    key = decrypt_text(app_key=attachment.message.conversation.app.app_key, value=key)
    with default_storage.open(key, "rb") as stored:
        return decrypt_bytes(app_key=attachment.message.conversation.app.app_key, value=stored.read())
