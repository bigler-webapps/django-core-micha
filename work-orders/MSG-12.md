# WORK ORDER MSG-12 (django-core-micha) — attachments never expose a display filename

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Operator report 2026-08-03 from staging, screenshot: an
attachment message rendered as a raw UUID link (`1b554f8f-bc50-41b4-bbe1-2e56ddd71c10 herunterladen`)
instead of a filename. Companion to `ui-core-micha` `MSG-6h`, which redesigns the attachment display
(thumbnail + lightbox + right-click download, WhatsApp-style) — that redesign needs a real display
name to work with, which this WO supplies.

## TIER
Tier 2 — shared-core, consumed by every app.

## ROOT CAUSE

`MessageAttachment` (`models.py:170-189`) already stores the real, sanitized upload name:

```python
filename = EncryptedTextField(app_key_accessor="messaging_app_key")
```

written at upload time (`attachments.py:123`):

```python
filename=sanitize_filename(getattr(upload, "name", "file"))
```

— this is **not** the storage key (`blob_key` is the separate, obfuscated field for that). But
`serialize_attachment` (`serializers.py:33-39`) never includes it:

```python
def serialize_attachment(attachment):
    base = f"/api/messaging/attachments/{attachment.id}/"
    return {"id": str(attachment.id), "content_type": attachment.content_type,
            "byte_size": attachment.byte_size, "order": attachment.order,
            "scan_state": attachment.scan_state, "url": base,
            "thumbnail_url": f"{base}thumbnail/" if attachment.thumbnail_key else None}
```

`ui-core-micha`'s `AttachmentList.jsx` does `nameOf = attachment.filename || attachment.name ||
attachment.id` — since the API response never has `filename`, it **always** falls through to the raw
UUID `id`. This is a pure serialization gap, not a storage or privacy gap — the real name is already
sitting on the model, sanitized, ready to expose.

## FIX

Add `"filename": attachment.filename` to `serialize_attachment`'s returned dict. Nothing else changes
— `blob_key` (the actual storage path) stays unexposed, as does everything else about how the file is
stored or served; `url`/`thumbnail_url` remain the only access paths (encrypted storage, download-only,
per the existing design principle in `test_attachments.py`).

## NON-GOALS / DO NOT TOUCH
- Do not change `blob_key`, storage, encryption, or the download/thumbnail endpoints themselves.
- Do not change `sanitize_filename` or upload validation.
- No model or migration changes — `filename` already exists on the model.

## RISKS
- None identified beyond the standard shared-core blast radius: every app consuming
  `serialize_attachment`'s output gains one new field. Purely additive — no existing consumer reads or
  depends on `filename`'s absence.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. `serialize_attachment` includes `filename` matching the sanitized upload name.
2. The full attachment-upload response (`ConversationAttachmentView`, existing
   `test_attachment_upload_with_matching_string_client_request_id_succeeds`-style fixture, or a new
   dedicated one) includes `filename` in the returned message's `attachments` array.

## TEST SCOPE FOR THE GATE (orchestrator)
`messaging/` only.

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `main`. Publish + version bump (patch)
per the repo's release flow.

## MINI-HANDOVER (pastable)

> Repo: `C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `main`). Work order:
> `work-orders/MSG-12.md` — read it fully, then follow the `orchestrate-codex` skill.
>
> One-line fix: add `"filename": attachment.filename` to `serialize_attachment` (`serializers.py:33-39`).
> The model already stores the real sanitized upload name; the serializer just never returns it.
> Companion to `ui-core-micha` `MSG-6h` (attachment gallery redesign), which consumes this field.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
