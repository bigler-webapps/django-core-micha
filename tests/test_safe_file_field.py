import io
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models

from django_core_micha.fields import SafeFileField
from django_core_micha.fields.safe_file import _UploadContentValidator
from django_core_micha.validators.upload import (
    MAX_FILENAME_LENGTH,
    sanitize_filename,
    validate_upload,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
    b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDAT"
    b"x\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00" + b"\x08" * 64
    + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    + b"\xff\xc4\x00\x14\x00\x01" + b"\x00" * 15 + b"\x08"
    + b"\xff\xc4\x00\x14\x10\x01" + b"\x00" * 15 + b"\x00"
    + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9"
)

PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF\n"

PLAIN_TEXT_BYTES = b"this is just text, no magic bytes here\n"

SVG_BYTES = (
    b'<?xml version="1.0"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="red"/></svg>'
)


def _upload(name, data, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class TestSanitizeFilename:
    def test_path_traversal_stripped(self):
        out = sanitize_filename("../../etc/passwd.png")
        assert "/" not in out
        assert ".." not in out
        assert out.count(".") == 1
        assert out.endswith(".png")
        assert out.startswith("passwd-")

    def test_double_extension_kept_only_last(self):
        out = sanitize_filename("script.php.png")
        assert out.count(".") == 1
        assert out.endswith(".png")
        assert "php" in out  # part of stem, slugified

    def test_spaces_replaced(self):
        out = sanitize_filename("foo bar baz.png")
        assert " " not in out
        assert out.endswith(".png")
        assert out.startswith("foo-bar-baz-")

    def test_no_extension(self):
        out = sanitize_filename("README")
        assert "." not in out
        assert out.startswith("readme-")

    def test_empty_stem_uses_fallback(self):
        out = sanitize_filename(".hidden")
        assert out.startswith("hidden-")

    def test_empty_input_uses_fallback(self):
        out = sanitize_filename("")
        assert out.startswith("file-")

    def test_unicode_normalized(self):
        out = sanitize_filename("Über_File.png")
        assert all(ord(c) < 128 for c in out)
        assert out.endswith(".png")

    def test_windows_path_traversal_stripped(self):
        out = sanitize_filename("..\\..\\etc\\passwd.png")
        assert "\\" not in out
        assert "/" not in out
        assert ".." not in out
        assert out.count(".") == 1
        assert out.endswith(".png")

    def test_length_capped(self):
        long_name = "a" * 500 + ".png"
        out = sanitize_filename(long_name)
        assert len(out) <= MAX_FILENAME_LENGTH

    def test_hash_makes_collisions_unlikely(self):
        a = sanitize_filename("same.png")
        b = sanitize_filename("same.png")
        # Different time_ns → different hash suffix
        assert a != b


class TestValidateUpload:
    def test_accepts_png(self):
        f = _upload("ok.png", PNG_BYTES)
        validate_upload(f, allowed_mimes={"image/png"}, max_size=10_000)

    def test_rejects_size_over_limit(self):
        f = _upload("big.png", PNG_BYTES)
        with pytest.raises(ValidationError):
            validate_upload(f, allowed_mimes={"image/png"}, max_size=10)

    def test_rejects_mime_not_in_allowlist(self):
        f = _upload("ok.pdf", PDF_BYTES)
        with pytest.raises(ValidationError):
            validate_upload(f, allowed_mimes={"image/png"}, max_size=10_000)

    def test_rejects_undetectable(self):
        f = _upload("ok.txt", PLAIN_TEXT_BYTES)
        with pytest.raises(ValidationError):
            validate_upload(f, allowed_mimes={"image/png"}, max_size=10_000)

    def test_rejects_svg_by_default(self):
        f = _upload("evil.svg", SVG_BYTES)
        with pytest.raises(ValidationError):
            validate_upload(
                f,
                allowed_mimes={"image/png", "image/jpeg"},
                max_size=10_000,
            )

    def test_fake_extension_rejected_by_content(self):
        # PDF content named as .png is detected by magic bytes as PDF
        f = _upload("evil.png", PDF_BYTES)
        with pytest.raises(ValidationError):
            validate_upload(f, allowed_mimes={"image/png"}, max_size=10_000)

    def test_file_pointer_restored(self):
        f = _upload("ok.png", PNG_BYTES)
        f.seek(5)
        try:
            validate_upload(
                f, allowed_mimes={"image/png"}, max_size=10_000
            )
        except ValidationError:
            pytest.fail("Should not raise")
        # Pointer should be at 5 again after the helper
        assert f.tell() == 5

    def test_accepts_jpeg(self):
        f = _upload("ok.jpg", JPEG_BYTES)
        validate_upload(f, allowed_mimes={"image/jpeg"}, max_size=10_000)


class TestSafeFileFieldInit:
    def test_requires_allowed_mimes(self):
        with pytest.raises(TypeError):
            SafeFileField(max_size=1024)

    def test_requires_max_size(self):
        with pytest.raises(TypeError):
            SafeFileField(allowed_mimes={"image/png"})

    def test_stores_kwargs(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=1024)
        assert f.allowed_mimes == frozenset({"image/png"})
        assert f.max_size == 1024
        assert f.sanitize_filename is True

    def test_sanitize_filename_optional(self):
        f = SafeFileField(
            allowed_mimes={"image/png"},
            max_size=1024,
            sanitize_filename=False,
        )
        assert f.sanitize_filename is False

    def test_validator_attached(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=1024)
        validators = [
            v
            for v in f.validators
            if isinstance(v, _UploadContentValidator)
        ]
        assert len(validators) == 1
        assert validators[0].allowed_mimes == frozenset({"image/png"})
        assert validators[0].max_size == 1024


class TestSafeFileFieldValidatorBehaviour:
    def test_validator_rejects_bad_upload(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        upload = _upload("ok.pdf", PDF_BYTES)
        validator = next(
            v
            for v in f.validators
            if isinstance(v, _UploadContentValidator)
        )
        with pytest.raises(ValidationError):
            validator(upload)

    def test_validator_accepts_good_upload(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        upload = _upload("ok.png", PNG_BYTES)
        validator = next(
            v
            for v in f.validators
            if isinstance(v, _UploadContentValidator)
        )
        validator(upload)

    def test_validator_skips_non_upload_value(self):
        """Existing FieldFile reference (legacy record) must not be validated."""
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        validator = next(
            v
            for v in f.validators
            if isinstance(v, _UploadContentValidator)
        )

        class FakeFieldFile:
            file = None
            name = "legacy/already-stored.png"

        # Must not raise even though content is unavailable
        validator(FakeFieldFile())

    def test_validator_unwraps_fieldfile_with_uploaded_file(self):
        """FieldFile wrapping a fresh UploadedFile must reach validate_upload."""
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        validator = next(
            v
            for v in f.validators
            if isinstance(v, _UploadContentValidator)
        )

        class FakeFieldFile:
            def __init__(self, inner):
                self.file = inner

        # Bad content wrapped in FieldFile-like → must still reject
        bad = _upload("evil.png", PDF_BYTES)
        with pytest.raises(ValidationError):
            validator(FakeFieldFile(bad))

        # Good content wrapped in FieldFile-like → must accept
        good = _upload("ok.png", PNG_BYTES)
        validator(FakeFieldFile(good))


class TestSafeFileFieldPreSave:
    def _mock_field_file(self, *, name, committed):
        m = MagicMock()
        m.__bool__ = lambda self: True
        m._committed = committed
        m.name = name
        return m

    def test_sanitizes_fresh_upload(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        mock_file = self._mock_field_file(
            name="../dangerous path/My File.png", committed=False
        )
        with patch.object(
            models.FileField, "pre_save", return_value=mock_file
        ):
            result = f.pre_save(MagicMock(), add=True)
        assert result is mock_file
        assert "/" not in mock_file.name
        assert " " not in mock_file.name
        assert ".." not in mock_file.name
        assert mock_file.name.startswith("my-file-")
        assert mock_file.name.endswith(".png")

    def test_skips_committed_file(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        original = "legacy/already-stored.png"
        mock_file = self._mock_field_file(name=original, committed=True)
        with patch.object(
            models.FileField, "pre_save", return_value=mock_file
        ):
            f.pre_save(MagicMock(), add=False)
        assert mock_file.name == original

    def test_skips_when_sanitize_disabled(self):
        f = SafeFileField(
            allowed_mimes={"image/png"},
            max_size=10_000,
            sanitize_filename=False,
        )
        original = "Original Name.png"
        mock_file = self._mock_field_file(name=original, committed=False)
        with patch.object(
            models.FileField, "pre_save", return_value=mock_file
        ):
            f.pre_save(MagicMock(), add=True)
        assert mock_file.name == original

    def test_handles_none_file(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=10_000)
        with patch.object(models.FileField, "pre_save", return_value=None):
            result = f.pre_save(MagicMock(), add=True)
        assert result is None


class TestSafeFileFieldDeconstruct:
    def test_roundtrip_kwargs(self):
        f = SafeFileField(
            allowed_mimes={"image/png", "image/jpeg"}, max_size=2048
        )
        name, path, args, kwargs = f.deconstruct()
        assert kwargs["allowed_mimes"] == {"image/png", "image/jpeg"}
        assert kwargs["max_size"] == 2048
        # _UploadContentValidator should not leak into kwargs
        for v in kwargs.get("validators", []):
            assert not isinstance(v, _UploadContentValidator)

    def test_sanitize_filename_false_kept(self):
        f = SafeFileField(
            allowed_mimes={"image/png"},
            max_size=1024,
            sanitize_filename=False,
        )
        _, _, _, kwargs = f.deconstruct()
        assert kwargs["sanitize_filename"] is False

    def test_sanitize_filename_default_omitted(self):
        f = SafeFileField(allowed_mimes={"image/png"}, max_size=1024)
        _, _, _, kwargs = f.deconstruct()
        assert "sanitize_filename" not in kwargs
