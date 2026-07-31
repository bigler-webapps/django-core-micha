# Messaging Platform — Design

Status: **Proposed binding design** (MSG-1, 2026-07-31). Approval Gate #1 and the spesix demand gate remain open. `notifications-platform.md` remains authoritative for notification routing/delivery; this document defines Layer 3.

> MSG-2/3/4 must not start until the operator passes the [spesix demand gate](#go--no-go). Non-confirmation shelves this design; it never authorises a weaker build.

## Principle and boundary

Messaging is durable shared conversation data; notifications are recipient-specific delivery signals. Messaging uses the existing single Layer-1 socket, produces `notify()` signals, owns no second consumer, and has no client-to-server WebSocket path: every write is authenticated REST. Each registered `app_key` is an isolated tenant; cross-app messaging, key reuse and participant lookup are forbidden.

V1 includes jg parity (direct/group/broadcast/managed, reactions, one-level replies, polls, images, author edit/soft-delete, read ticks, mute/unread, encryption/rotation, break-glass audit, notifications, nullable-sender system messages, per-scope config) and object threads, delivered watermarks, retention, channel preferences, archive and files. Search, typing (v1.x REST-ping candidate), scanner infrastructure, inbound WS and jg migration implementation are non-goals.

## Domain model

All public IDs are opaque UUIDs; timestamps are UTC. `app` means `MessagingApp(app_key unique, active, keyset_id, created_at)`.

| Model | Fields / constraints |
|---|---|
| `MessagingScope` | `app`, `kind` (`container`,`object`,`global`), nullable `content_type`+`object_id` GenericFK, JSON `config`, timestamps. Global has neither ref; others require both; unique `(app,kind,content_type,object_id)`. Config holds `dm_policy`, `group_chat_enabled`, `everyone_can_post` and app-validated keys. |
| `Conversation` | `app`, `scope`, `kind` (`direct`,`group`,`broadcast`,`managed`,`object_thread`), encrypted nullable `title`, `external_key`, canonical direct `user_low/user_high`, `last_message_at`, `retention_policy` (`never` default), nullable `delete_after`, timestamps. Unique direct `(app,scope,user_low,user_high)` and managed/broadcast `(app,scope,kind,external_key)`; object threads require an object scope. DMs may be container-scoped or global. |
| `ConversationParticipant` | `conversation,user`, `membership_source` (`manual`,`provider`), `last_delivered_at`, `last_read_at`, `muted`, nullable `email_enabled/push_enabled` overrides, `archived_at`, `joined_at`, `removed_at`; unique pair. Removed rows retain audit but are not live. |
| `Message` | `conversation`, nullable `sender`, `kind` (`chat`,`announcement`,`poll`,`system`), encrypted `title/body/link_target`, nullable `reply_to`, `client_request_id`, `edited_at/deleted_at/deleted_by`, timestamps. Reply target is same-conversation root only (depth one); idempotency unique `(conversation,sender,client_request_id)`. |
| `MessageReaction` | `message,user,emoji` (validated grapheme, max 16), timestamps; unique triple. |
| `MessageAttachment` | `message`, encrypted blob key and filename, `content_type,byte_size,sha256,order`, image dimensions/encrypted thumbnail key, `scan_state` and metadata. Blob and thumbnail use the app ring. |
| `MessageThreadReceipt` | `root,user,last_read_at`, unique pair. |
| `Poll`, `PollOption`, `PollVote` | Poll: one-to-one message, encrypted question, `allow_multiple,created_by,closed_at`; option: encrypted text/order; vote: option/user/time. Service transaction enforces one vote for single polls. |
| `MessagingAuditEvent` | `app,actor,action,target GenericFK,reason,request/correlation metadata,time`; mandatory for break-glass attempts, no plaintext. |

**Relationship to the existing `django_core_micha.auditlog` subpackage:** deliberately a separate model, not a reuse. `auditlog.AuditEvent` is a generic *change*-audit facility — apps `register()` a model once, listing `redact_fields`, and get automatic signal-driven logging of writes to that model; it has no per-app tenant field and no target GenericFK, and one model can only be registered once process-wide. Messaging's break-glass need is an explicit *read*-access log (who viewed decrypted content and why), not a write audit, and needs per-app tenant scoping (`app`) that `auditlog.AuditEvent` does not carry — so it does not fit the registration model. `MessagingAuditEvent` is therefore its own table, populated by an explicit call from the break-glass code path (never signal-driven), not a second consumer of the generic registry.

The four future seams are schema now: object thread, delivered watermark, retention fields, and participant channel/archive state. `last_delivered_at` means current authenticated realtime fan-out, not email/push acknowledgement. `last_read_at` is jg's explicit-read semantic. `all_read` excludes current non-sender participants. Only moderators see per-recipient detail for non-DMs; **DMs never expose recipient detail**, including to moderators. Archive is participant-local and clears on a new non-self message. `never` is v1's only retention policy.

## App hook contract

Each app registers one deterministic, tenant-safe provider; dcm does not import app models or attach app signals.

```python
class MessagingPolicy(Protocol):
 def can_open_direct(self, *, actor: User, target: User, scope: ScopeRef | None) -> bool: ...
 def can_view_conversation(self, *, actor: User, conversation: ConversationRef) -> bool: ...
 def can_post(self, *, actor: User | None, conversation: ConversationRef, message_kind: str) -> bool: ...
 def moderation_rights(self, *, actor: User, conversation: ConversationRef, message: MessageRef | None=None) -> frozenset[str]: ...
 def resolve_recipients(self, *, conversation: ConversationRef, trigger: Literal['create','message','membership_refresh']) -> Iterable[User]: ...
 def provision_membership(self, *, conversation: ConversationRef, trigger: Literal['scope_created','domain_changed','reconcile']) -> MembershipSnapshot: ...
 def validate_scope(self, *, actor: User, scope: ScopeRef, conversation_kind: str) -> ScopeConfig: ...
```

Self-DM rejection is core-owned. Moderation rights are `edit_any`, `delete_any`, `read_receipt_detail`, `manage_config`, `open_broadcast`, `open_group`, `create_managed`; authors retain edit/delete of own non-deleted messages. Recipient resolution is live before every send: dcm intersects it with current participants and excludes sender/muted recipients for notifications. Provisioning returns `{members, external_key, remove_absent}`; dcm upserts provider members and marks absent provider rows removed only when requested. jg `event_all`/`event_team` becomes managed conversations reconciled by its Event/Registration/Membership signals. spesix expense-claim derivation uses this identical seam.

## Encryption-at-rest — hard gate

`sync-secrets` provisions a distinct ordered Fernet ring per app: `MESSAGING_KEYRINGS[app_key] = [primary, old...]`. Missing/malformed/empty or shared rings fail registration closed. MultiFernet encrypts all text, poll content, filenames, blobs and thumbnails; no plaintext fallback exists. Rotation: provision new key; deploy `[new,old...]`; resumably re-encrypt text/blobs under locks; verify; remove old later. Storage decrypts only in authenticated conversation-policy views; no generic `/media/`, public URL, storage bypass or inline foreign-content rendering. Break-glass requires policy capability, explicit reason and `MessagingAuditEvent` (including denial), never routine admin rendering.

If MSG-2 cannot implement rings through `sync-secrets` without cross-app exposure, STOP and offer only per-app secret settings, a KMS envelope-key adapter, or messaging deferral—never a shared key.

## REST contract

Base `/api/messaging/`; authenticated users only. Every read uses `can_view_conversation`. Lists return `{results,next_cursor}` with signed opaque `(created_at,id)` cursor, default/max 50/100; bad cursor is 400. `Idempotency-Key` plus `client_request_id` protects optimistic POST retries.

| Endpoint | Contract / permission |
|---|---|
| `GET conversations/?scope_kind=&content_type=&object_id=&include_archived=&cursor=` | summaries plus participant-local unread/archive/channels; validated scope/viewer. |
| `POST conversations/direct/` | `{target_user_id,scope?}`; omitted scope = global; `can_open_direct`. |
| `POST conversations/group|broadcast|managed|object-thread/` | Group `{scope,title,participant_ids}`; broadcast/managed `{scope,external_key?}`; object requires object scope. Respective scope config/capability; provider owns managed/object membership. |
| `GET/PATCH conversations/{id}/config/` | Scope config; `manage_config`. |
| `POST conversations/{id}/archive/` / `preferences/` | `{archived}`; `{muted?,email_enabled?:bool|null,push_enabled?:bool|null}`; current participant. |
| `GET/POST conversations/{id}/messages/?cursor=` | roots ordered chronologically; POST `{kind,body?,title?,link_target?,reply_to?,client_request_id?}`; viewer / `can_post`. |
| `POST conversations/{id}/attachments/` | multipart `files[]`, optional body/reply/client id → chat message + attachments; `can_post`. |
| `POST conversations/{id}/polls/` | `{question,options,allow_multiple,client_request_id?}`; `can_post`. |
| `GET/PATCH/DELETE messages/{id}/` | PATCH body/title/link only; DELETE soft delete; viewer then author or `edit_any/delete_any`. |
| `POST messages/{id}/reactions/`, `DELETE messages/{id}/reactions/{emoji}/` | `{emoji}` / aggregate projection; viewer/current participant. |
| `GET messages/{root_id}/thread/?cursor=`, `POST messages/{root_id}/thread/read/` | replies / `{read_at?}` advances own thread receipt; viewer/current participant. |
| `POST conversations/{id}/read/`, `GET messages/{id}/read-status/` | `{read_at?}` (server clamps) / `{all_read,delivered_count,recipient_detail?}`; detail requires `read_receipt_detail`, never direct. |
| `POST polls/{id}/vote/`, `POST polls/{id}/close/` | `{option_ids}` / current poll; participant/open poll, then author or moderator to close. |
| `GET attachments/{id}/` / `thumbnail/`; `GET unread-count/` | authenticated decrypt-and-stream / `{unread_count,by_conversation}`. |

Inaccessible objects are 404; capability failure is 403. Polls are immutable except vote/close; messages cannot alter sender/conversation/kind/reply/attachments.

## Realtime

Every frame is `{envelope:'messaging',type,event_id,app_key,conversation_id,occurred_at,...}` and is emitted only to live policy-resolved users. Frames: `conversation_upsert`, `conversation_archived`, `message`, `message_edited`, `message_deleted`, `attachment_ready`, `reaction`, `poll_updated`, `delivered`, `read_state`, `thread_read_state`, `participant_changed`. Message frames carry safe serialization/API attachment URLs; receipt frames carry only aggregate unless permitted, never direct detail. All mutations commit before fan-out; handlers deduplicate `event_id`. Reconnect refetches REST state/cursors. ucm must destructure `const { subscribe } = useRealtime()` and depend on `subscribe`, never the recreated context object.

## Notification contract

Per-app event type registration is `mode='event'`, messaging category, `resolution='user-done'`, eligible/default `[email,push]`, no chip, `feed_visible=False`. `notify()` occurs inside the message transaction/on-commit boundary after live resolution; sender/muted users are excluded. `content` has i18n keys, stable link and non-sensitive IDs only; sender/title/body excerpt/poll text/filename live exclusively in `transient=`. Existing dedup is `(type,notifiable)`: notifiable is message/poll, never recipient/plaintext.

| Event | channels | persistent | feed | safe contract |
|---|---|---|---|---|
| New chat/announcement/poll/attachment message | email,push | yes: one `Notification` + recipient/delivery rows | no | message dedup, sensitive preview transient |
| edit/delete; reaction; poll vote/close; delivery/read; archive/membership | none | no | n/a | realtime only; later alert needs a new type/design |

Delivery failure cannot roll back the durable message.

## Attachments

Use `validators.upload.validate_upload` magic-byte validation. Accept PDF, OOXML (`docx/xlsx/pptx`), ODF (`odt/ods/odp`), legacy Office only with reliable signature, PNG/JPEG/GIF/WebP; reject MIME mismatch, archives, executables, HTML/SVG and unknown bytes. Limit is 25 MiB/file (policy may lower only). Images decode, EXIF-strip, safe re-encode and thumbnail before encryption. Other files are `attachment` download-only with `nosniff`; no foreign inline rendering.

**Container-format caveat (MSG-2 pre-check):** OOXML and ODF files are themselves ZIP containers — the same leading magic bytes as a generic ZIP archive that this allowlist must reject. `validators.upload.validate_upload` currently ships only an `IMAGE_DEFAULT_MIMES` default; an Office/PDF allowlist is new caller-supplied config, not an existing capability. Before MSG-2 relies on `filetype`-based detection for OOXML/ODF, confirm it actually distinguishes them from a bare ZIP (e.g. via internal member inspection — `word/`/`xl/`/`ppt/` entries for OOXML, the `mimetype` entry for ODF), not just the outer container signature. If `filetype`'s built-in detectors do not cover this reliably, `validate_upload` needs an explicit content-aware check for these types before archives can be safely rejected without also rejecting legitimate Office files.

Define, but do not install, `MessagingScanHook.scan(*, app_key, attachment_id, plaintext_path, declared_type) -> ScanResult`. It runs after validation before persistence when present; v1 has no scanner/queue and accepted files are `unscanned`.

## Volume, retention and operations

NOTIF-20/21's no-schedule decision was valid at low volume but no longer holds: 100 active users × 10 posts/day × 12 recipients = **12,000 recipient rows/day** (360k/month) and up to 720k delivery rows/month; a stress-case 500 active users × 50 posts/day × 50 recipients is **1,250,000/day** (37.5m/month). MSG-2 adds `notify(expires_at=...)`; `messaging.new_message` uses 30-day TTL for notification/delivery/recipient projections, never messages. Production janitor scheduling is a MSG-4 deploy gate, not dcm code. `scheduled_commands` is staging-only until `webapp-management/work-orders/CI-5.md`; without CI-5/equivalent scheduler MSG-4 is blocked. Monitor created/expired rows and >48h janitor lag.

## ucm surface

MSG-3 provides `MessagingProvider`, API adapter, normalized cache and `ConversationList`, `Thread`, `Composer`, `ReadTicks`, `ReactionBar`, `PollCard`, `AttachmentList`, config/preferences and conversation launchers. Host apps supply routing/display/scope pickers, not a forked state machine. The composer writes optimistic `client_request_id` rows, reconciles REST/WS confirmation and shows retry/error without duplicates. Thread history uses opaque cursors with infinite reverse scroll; reconnect refetches current list/thread/unread. Offline drafts may persist locally but send requires REST. Labels/status/validation ship de/en/fr.

## Paper tests

### jg shape

Event maps to container scope; event-scoped direct remains a direct pair under that scope. Group/broadcast map directly. `event_all`/`event_team` map to managed external keys and signal-triggered provider reconciliation. Event-manager rights map to moderation/config capabilities. Chat/announcement/poll/system, replies, reactions, votes/close, mute/unread, aggregate read and moderator detail all map directly; DM detail remains prohibited. Images keep normalize/EXIF-strip/encrypt/auth-stream. Sending resolves current recipients, excludes sender/muted users and uses the exact email/push, no-chip, feed-hidden `notify(transient=...)` recipe. No jg feature is lost.

### spesix shape

Expense claim `EC-42` is an object scope; its provider derives claimant, approver and finance reviewer and reconciles on assignment/status changes. Its object thread permits only policy-authorized viewing/posting; an encrypted receipt attachment is validated/streamed and alerts eligible unmuted non-senders. The same app opens a global DM (no scope) and an authorised global broadcast. All three ride identical REST and `messaging` frames. This is an assumption pending confirmation.

## Go / no-go

Before Gate #1 the operator must confirm: concrete expense-claim model/content type; participant rule and update triggers; moderation/broadcast roles; that MSG-4 ships object threads, global DMs and broadcast; timeline and volume. Any change returns to the operator. No confirmation: shelf/block MSG-1 and leave MSG-2–4 planned (or drop only by operator decision).

## Phase C and release plan

Phase C maps Event→container, pairs→scoped direct, group/broadcast→same kinds, event_all/team→managed keys. Preserve IDs/times/deletes/replies/receipts/reactions/polls; decrypt under retained jg ring and re-encrypt text/blobs under jg's dcm ring; cut over only after parity/count/decrypt reconciliation. Sketch only.

Phase B needs 10–14 chunks: MSG-2 4–5 (registry/keyrings/models; hooks/services; REST/WS; attachments/TTL); MSG-3 3–5 (provider/cache; list/thread pagination; composer/attachments; reactions/polls/ticks/preferences; i18n/PWA); MSG-4 3–4 (spesix provider/object scope; surfaces/notify; rollout scheduler). Staged commits and small sibling contract fixes are in-scope. For each release chunk: publish from main, independently live-check registry, then bump exact consumer pin and redeploy. Per-chunk scoped tests; assembled WO affected-area test gate plus one independent review, or explicitly staged independent reviews NOTIF-7-style. MSG-4 requires published MSG-2 then MSG-3, demand confirmation and production janitor resolution.
