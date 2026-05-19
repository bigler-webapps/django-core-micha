import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from django_core_micha.fields import SafeImageField
from django_core_micha.fields.safe_file import _UploadContentValidator
from django_core_micha.validators.upload import IMAGE_DEFAULT_MIMES

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
    b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDAT"
    b"x\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

SVG_BYTES = (
    b'<?xml version="1.0"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10"/></svg>'
)


def _upload(name, data):
    return SimpleUploadedFile(name, data)


class TestSafeImageFieldDefaults:
    def test_default_allowed_mimes(self):
        f = SafeImageField(max_size=1024)
        assert f.allowed_mimes == IMAGE_DEFAULT_MIMES

    def test_no_svg_in_defaults(self):
        f = SafeImageField(max_size=1024)
        assert "image/svg+xml" not in f.allowed_mimes

    def test_no_heic_in_defaults(self):
        f = SafeImageField(max_size=1024)
        assert "image/heic" not in f.allowed_mimes
        assert "image/heif" not in f.allowed_mimes

    def test_no_avif_in_defaults(self):
        f = SafeImageField(max_size=1024)
        assert "image/avif" not in f.allowed_mimes

    def test_requires_max_size(self):
        with pytest.raises(TypeError):
            SafeImageField()


class TestSafeImageFieldOverride:
    def test_custom_allowed_mimes_override(self):
        f = SafeImageField(
            allowed_mimes={"image/png"}, max_size=1024
        )
        assert f.allowed_mimes == frozenset({"image/png"})
        assert "image/jpeg" not in f.allowed_mimes

    def test_can_add_svg_explicitly(self):
        f = SafeImageField(
            allowed_mimes=IMAGE_DEFAULT_MIMES | {"image/svg+xml"},
            max_size=1024,
        )
        assert "image/svg+xml" in f.allowed_mimes


class TestSafeImageFieldValidation:
    def _validator(self, field):
        return next(
            v
            for v in field.validators
            if isinstance(v, _UploadContentValidator)
        )

    def test_accepts_png(self):
        f = SafeImageField(max_size=10_000)
        upload = _upload("logo.png", PNG_BYTES)
        self._validator(f)(upload)

    def test_rejects_svg_by_default(self):
        # Note: SVG is XML text. `filetype` v1.x does not have an SVG signature,
        # so detection returns None and the rejection path is "could not detect"
        # rather than "MIME not in allowlist". Operational outcome is the same:
        # SVG cannot be uploaded with the default image allowlist.
        f = SafeImageField(max_size=10_000)
        upload = _upload("icon.svg", SVG_BYTES)
        with pytest.raises(ValidationError):
            self._validator(f)(upload)

    def test_rejects_oversized(self):
        f = SafeImageField(max_size=10)
        upload = _upload("big.png", PNG_BYTES)
        with pytest.raises(ValidationError):
            self._validator(f)(upload)
