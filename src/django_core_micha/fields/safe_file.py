from django.core.files.uploadedfile import UploadedFile
from django.db import models

from django_core_micha.validators.upload import (
    IMAGE_DEFAULT_MIMES,
    sanitize_filename as _sanitize_filename_fn,
    validate_upload,
)


class _UploadContentValidator:
    def __init__(self, allowed_mimes, max_size):
        self.allowed_mimes = frozenset(allowed_mimes)
        self.max_size = int(max_size)

    def __call__(self, value):
        if isinstance(value, UploadedFile):
            target = value
        elif hasattr(value, "file") and isinstance(value.file, UploadedFile):
            target = value.file
        else:
            return
        validate_upload(
            target,
            allowed_mimes=self.allowed_mimes,
            max_size=self.max_size,
        )

    def __eq__(self, other):
        return (
            isinstance(other, _UploadContentValidator)
            and self.allowed_mimes == other.allowed_mimes
            and self.max_size == other.max_size
        )

    def __hash__(self):
        return hash((self.allowed_mimes, self.max_size))


_REQUIRED = object()


class SafeFileField(models.FileField):
    def __init__(
        self,
        *args,
        allowed_mimes=_REQUIRED,
        max_size=_REQUIRED,
        sanitize_filename=True,
        **kwargs,
    ):
        if allowed_mimes is _REQUIRED or max_size is _REQUIRED:
            raise TypeError(
                "SafeFileField requires allowed_mimes and max_size"
            )

        self.allowed_mimes = frozenset(allowed_mimes)
        self.max_size = int(max_size)
        self.sanitize_filename = bool(sanitize_filename)

        validators = list(kwargs.get("validators") or [])
        validators.append(
            _UploadContentValidator(self.allowed_mimes, self.max_size)
        )
        kwargs["validators"] = validators

        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["allowed_mimes"] = set(self.allowed_mimes)
        kwargs["max_size"] = self.max_size
        if not self.sanitize_filename:
            kwargs["sanitize_filename"] = False
        if "validators" in kwargs:
            kwargs["validators"] = [
                v
                for v in kwargs["validators"]
                if not isinstance(v, _UploadContentValidator)
            ]
            if not kwargs["validators"]:
                del kwargs["validators"]
        return name, path, args, kwargs

    def pre_save(self, model_instance, add):
        file = super().pre_save(model_instance, add)
        if (
            file
            and self.sanitize_filename
            and getattr(file, "_committed", True) is False
            and getattr(file, "name", None)
        ):
            file.name = _sanitize_filename_fn(file.name)
        return file


class SafeImageField(SafeFileField):
    def __init__(self, *args, allowed_mimes=None, max_size=_REQUIRED, **kwargs):
        if allowed_mimes is None:
            allowed_mimes = IMAGE_DEFAULT_MIMES
        super().__init__(
            *args, allowed_mimes=allowed_mimes, max_size=max_size, **kwargs
        )
