import uuid
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


@pytest.mark.django_db
def test_attachment_upload_with_matching_string_client_request_id_succeeds(message):
    """MSG-10 scope A: the attachment endpoint's client_request_id arrives as an
    unvalidated `str` from multipart body data (unlike the send-message/create-poll paths,
    which go through a UUIDField serializer first), and was compared with `!=` directly
    against the header's real `uuid.UUID` -- `str != UUID` is always True in Python, so
    this failed unconditionally before the fix, on the operator's exact request shape."""
    row, user = message
    conversation = row.conversation
    request_id = str(uuid.uuid4())
    good_file = upload(b"%PDF-1.4\n", "file.pdf", "application/pdf")
    request = APIRequestFactory().post(
        f"/conversations/{conversation.id}/attachments/",
        {"files[]": good_file, "client_request_id": request_id},
        format="multipart", HTTP_IDEMPOTENCY_KEY=request_id,
    )
    force_authenticate(request, user=user)
    response = ConversationAttachmentView.as_view()(request, conversation_id=conversation.id)
    assert response.status_code == 201


@pytest.mark.django_db
def test_attachment_upload_with_mismatched_client_request_id_is_still_rejected(message):
    """The coercion fix must not defeat the guard -- a genuinely mismatched id is still 400."""
    row, user = message
    conversation = row.conversation
    header_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    file = upload(b"%PDF-1.4\n", "file.pdf", "application/pdf")
    request = APIRequestFactory().post(
        f"/conversations/{conversation.id}/attachments/",
        {"files[]": file, "client_request_id": other_id},
        format="multipart", HTTP_IDEMPOTENCY_KEY=header_id,
    )
    force_authenticate(request, user=user)
    response = ConversationAttachmentView.as_view()(request, conversation_id=conversation.id)
    assert response.status_code == 400
    assert "client_request_id" in response.data


@pytest.mark.django_db
def test_attachment_upload_with_malformed_client_request_id_is_rejected(message):
    """A non-UUID body value must be rejected with a clear error, not silently swallowed
    into a pass-through (that would trade a visible outage for a silent idempotency hole)."""
    row, user = message
    conversation = row.conversation
    header_id = str(uuid.uuid4())
    file = upload(b"%PDF-1.4\n", "file.pdf", "application/pdf")
    request = APIRequestFactory().post(
        f"/conversations/{conversation.id}/attachments/",
        {"files[]": file, "client_request_id": "not-a-uuid"},
        format="multipart", HTTP_IDEMPOTENCY_KEY=header_id,
    )
    force_authenticate(request, user=user)
    response = ConversationAttachmentView.as_view()(request, conversation_id=conversation.id)
    assert response.status_code == 400
    assert "client_request_id" in response.data


@pytest.mark.django_db
def test_attachment_upload_with_malformed_client_request_id_and_no_header_is_a_400_not_a_500(message):
    """Independent-review finding: the coercion above only ran inside `if header:`, so a
    malformed body value with NO Idempotency-Key header still reached `send_message` as a
    raw string, which hit `Message.objects.filter(client_request_id=...)` -- a UUIDField
    lookup that raises Django's own uncaught `ValidationError`, a 500 not a 400. Coercion
    must be unconditional, not gated on the header's presence."""
    row, user = message
    conversation = row.conversation
    file = upload(b"%PDF-1.4\n", "file.pdf", "application/pdf")
    request = APIRequestFactory().post(
        f"/conversations/{conversation.id}/attachments/",
        {"files[]": file, "client_request_id": "not-a-uuid"},
        format="multipart",
    )
    force_authenticate(request, user=user)
    response = ConversationAttachmentView.as_view()(request, conversation_id=conversation.id)
    assert response.status_code == 400
    assert "client_request_id" in response.data


@pytest.mark.django_db
def test_serialize_attachment_includes_the_sanitized_upload_filename(message):
    """MSG-12: the model already stores the real, sanitized upload name (`filename`,
    distinct from `blob_key`, the obfuscated storage path) -- the serializer just never
    returned it, so every client fell back to displaying the raw attachment id."""
    from django_core_micha.messaging.serializers import serialize_attachment
    row, _ = message
    attachment = create_attachment(message=row, upload=upload(b"%PDF-1.4\n", "My Report.pdf", "application/pdf"), order=0)
    result = serialize_attachment(attachment)
    assert result["filename"] == attachment.filename
    assert result["filename"] not in (None, "")
    assert result["filename"] != str(attachment.id)


@pytest.mark.django_db
def test_attachment_upload_response_includes_filename_for_the_client_to_render(message):
    row, user = message
    conversation = row.conversation
    file = upload(b"%PDF-1.4\n", "vacation-photo.pdf", "application/pdf")
    request = APIRequestFactory().post(f"/conversations/{conversation.id}/attachments/", {"files[]": file}, format="multipart")
    force_authenticate(request, user=user)
    response = ConversationAttachmentView.as_view()(request, conversation_id=conversation.id)
    assert response.status_code == 201
    [attachment_data] = response.data["attachments"]
    assert attachment_data["filename"]
    assert attachment_data["filename"] != attachment_data["id"]
