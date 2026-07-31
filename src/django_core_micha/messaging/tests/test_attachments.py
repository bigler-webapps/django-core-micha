from io import BytesIO
from zipfile import ZipFile

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.messaging.attachments import ALLOWED_MIMES, attachment_bytes, create_attachment
from django_core_micha.messaging.crypto import register_messaging_app
from django_core_micha.messaging.models import Conversation, ConversationParticipant, Message, MessagingApp, MessagingScope
from django_core_micha.messaging.policy import MembershipSnapshot, register_messaging_policy, unregister_messaging_policy
from django_core_micha.messaging.views import AttachmentDownloadView, ConversationAttachmentView


class Policy:
    def can_open_direct(self, **kwargs): return True
    def can_view_conversation(self, **kwargs): return True
    def can_post(self, **kwargs): return True
    def moderation_rights(self, **kwargs): return frozenset()
    def resolve_recipients(self, **kwargs): return []
    def provision_membership(self, **kwargs): return MembershipSnapshot([])
    def validate_scope(self, **kwargs): return {}


@pytest.fixture
def message(db, tmp_path):
    key = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"attachments-app": [key]}, MEDIA_ROOT=tmp_path):
        register_messaging_app("attachments-app")
        app = MessagingApp.objects.create(app_key="attachments-app", keyset_id="test")
        scope = MessagingScope.objects.create(app=app, kind="global")
        user = get_user_model().objects.create_user(username="attachment-user")
        conversation = Conversation.objects.create(app=app, scope=scope, kind="group")
        ConversationParticipant.objects.create(conversation=conversation, user=user)
        register_messaging_policy(app.app_key, Policy())
        yield Message.objects.create(conversation=conversation, sender=user, body="attachment"), user
    unregister_messaging_policy("attachments-app")


def upload(data, name, mime):
    return SimpleUploadedFile(name, data, content_type=mime)


@pytest.mark.django_db
def test_attachment_rejects_bare_zip_html_executable_and_mime_mismatch(message):
    row, _ = message
    cases = [
        upload(b"PK\\x03\\x04bare zip", "bad.zip", "application/zip"),
        upload(b"<html><body>x</body></html>", "bad.html", "text/html"),
        upload(b"MZ" + b"x" * 512, "bad.exe", "application/octet-stream"),
    ]
    for file in cases:
        with pytest.raises(Exception): create_attachment(message=row, upload=file, order=0)
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 40
    with pytest.raises(Exception): create_attachment(message=row, upload=upload(png, "wrong.pdf", "application/pdf"), order=0)


@pytest.mark.django_db
def test_attachment_allowlist_accepts_pdf_ooxml_and_odf(message):
    row, _ = message
    cases = [(b"%PDF-1.4\n", "file.pdf", "application/pdf")]
    for prefix, name, mime in (("word/", "file.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), ("xl/", "file.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), ("ppt/", "file.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")):
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("[Content_Types].xml", b""); archive.writestr("_rels/.rels", b""); archive.writestr(prefix + "document.xml", b"")
        cases.append((stream.getvalue(), name, mime))
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("mimetype", b"application/vnd.oasis.opendocument.text"); archive.writestr("content.xml", b"")
    cases.append((stream.getvalue(), "file.odt", "application/vnd.oasis.opendocument.text"))
    for order, (data, name, mime) in enumerate(cases):
        attachment = create_attachment(message=row, upload=upload(data, name, mime), order=order)
        assert attachment.content_type in ALLOWED_MIMES


@pytest.mark.django_db
def test_image_attachment_is_reencoded_encrypted_and_download_only(message):
    row, user = message
    from PIL import Image
    image = Image.new("RGB", (8, 6), "red"); raw = BytesIO(); image.save(raw, format="JPEG", exif=b"Exif\0\0junk")
    attachment = create_attachment(message=row, upload=upload(raw.getvalue(), "secret.jpg", "image/jpeg"), order=0)
    assert attachment.scan_state == "unscanned" and attachment.thumbnail_key
    assert attachment.filename != "secret.jpg"
    assert attachment_bytes(attachment).startswith(b"\xff\xd8")
    request = APIRequestFactory().get(f"/attachments/{attachment.id}/"); force_authenticate(request, user=user)
    response = AttachmentDownloadView.as_view()(request, attachment_id=attachment.id)
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_configured_scan_hook_is_called(message, monkeypatch):
    row, _ = message
    calls = []
    class Hook:
        def scan(self, **kwargs):
            calls.append(kwargs); return {"state": "clean", "metadata": {"engine": "test"}}
    with override_settings(MESSAGING_SCAN_HOOK=Hook()):
        from PIL import Image
        image = Image.new("RGB", (2, 2)); raw = BytesIO(); image.save(raw, format="PNG")
        attachment = create_attachment(message=row, upload=upload(raw.getvalue(), "image.png", "image/png"), order=0)
    assert calls and attachment.scan_state == "clean" and attachment.scan_metadata == {"engine": "test"}


@pytest.mark.django_db
def test_configured_scan_hook_rejection_blocks_persistence(message):
    row, _ = message

    class RejectingHook:
        def scan(self, **kwargs):
            return {"state": "rejected", "metadata": {"reason": "malware-signature"}}

    with override_settings(MESSAGING_SCAN_HOOK=RejectingHook()):
        with pytest.raises(Exception):
            create_attachment(message=row, upload=upload(b"%PDF-1.4\n", "quarantine.pdf", "application/pdf"), order=0)
    assert not row.attachments.exists()


@pytest.mark.django_db
def test_attachment_upload_rejection_is_a_400_not_a_500(message):
    """Regression: attachments.py raises django.core.exceptions.ValidationError, which
    DRF's stock exception handler does NOT translate to a Response (it only special-cases
    Http404 and django.core.exceptions.PermissionDenied) — ConversationAttachmentView must
    translate it itself or every rejected upload crashes with an unhandled 500."""
    row, user = message
    conversation = row.conversation
    bad_file = upload(b"PK\x03\x04bare zip", "bad.zip", "application/zip")
    request = APIRequestFactory().post(
        f"/conversations/{conversation.id}/attachments/", {"files[]": bad_file}, format="multipart"
    )
    force_authenticate(request, user=user)
    response = ConversationAttachmentView.as_view()(request, conversation_id=conversation.id)
    assert response.status_code == 400
    assert "files" in response.data
    # The message itself never persisted either — a rejected attachment must not
    # leave a bodyless "message" behind.
    assert not Message.objects.filter(conversation=conversation).exclude(pk=row.pk).exists()
