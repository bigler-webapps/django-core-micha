import hashlib
import os
import time

import filetype
from django.core.exceptions import ValidationError
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

MAX_FILENAME_LENGTH = 80

IMAGE_DEFAULT_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)


def detect_mime(file_obj):
    if hasattr(file_obj, "tell"):
        try:
            pos = file_obj.tell()
        except (OSError, ValueError):
            pos = None
    else:
        pos = None

    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except (OSError, ValueError):
            pass

    try:
        try:
            kind = filetype.guess(file_obj)
        except Exception:
            kind = None
    finally:
        if pos is not None:
            try:
                file_obj.seek(pos)
            except (OSError, ValueError):
                pass

    return kind.mime if kind is not None else None


def sanitize_filename(original_name):
    base = os.path.basename(str(original_name or ""))
    base = base.lstrip(".")

    if "." in base:
        stem, _, ext = base.rpartition(".")
    else:
        stem, ext = base, ""

    stem_slug = slugify(stem)
    if not stem_slug:
        stem_slug = "file"

    ext_slug = slugify(ext)

    hash_input = f"{original_name}-{time.time_ns()}".encode("utf-8", errors="replace")
    suffix = hashlib.sha256(hash_input).hexdigest()[:8]

    if ext_slug:
        candidate = f"{stem_slug}-{suffix}.{ext_slug}"
    else:
        candidate = f"{stem_slug}-{suffix}"

    if len(candidate) > MAX_FILENAME_LENGTH:
        ext_part = f".{ext_slug}" if ext_slug else ""
        hash_part = f"-{suffix}"
        available = MAX_FILENAME_LENGTH - len(hash_part) - len(ext_part)
        if available < 1:
            available = 1
        stem_slug = stem_slug[:available]
        candidate = f"{stem_slug}{hash_part}{ext_part}"

    return candidate


def validate_upload(file_obj, *, allowed_mimes, max_size):
    size = getattr(file_obj, "size", None)
    if size is None and hasattr(file_obj, "__len__"):
        try:
            size = len(file_obj)
        except TypeError:
            size = None
    if size is not None and size > max_size:
        raise ValidationError(
            _("File size %(size)d bytes exceeds maximum %(max)d bytes."),
            params={"size": size, "max": max_size},
        )

    detected = detect_mime(file_obj)
    if detected is None:
        raise ValidationError(
            _("Could not detect file type from content. Upload rejected.")
        )
    if detected not in allowed_mimes:
        raise ValidationError(
            _("File type %(mime)s is not allowed."),
            params={"mime": detected},
        )
