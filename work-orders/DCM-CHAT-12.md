# DCM-CHAT-12 — Shared chats in a scope

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Stage 2 of the shared chat for survey_app: it prepares `survey_app/SVA-AIB-45`. Companion: `DCM-CHAT-11`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The chat core makes every chat visible to its owner only. survey_app's AI builder works differently today: its conversation belongs to the survey's working draft, and every editor of the survey sees and continues it. The operator decided that this shared visibility stays.

Expected outcome: an app can declare scopes whose chats are shared. Every user who passes the scope permission can see and use them. Owner-only remains the default for every scope the app does not declare as shared, so cockpit and maana are unchanged.

## Operator decisions this Envelope records (2026-10-15)

- The AI-builder chats of a survey are visible to all editors of that survey.

## Scope

- **Declaring shared scopes:** an optional app hook decides, per scope, whether the scope's chats are shared. Without it, no scope is shared.
- **Access in a shared scope:** for every user who passes the scope permission, these work over REST and WebSocket alike:
  - listing, reading, sending;
  - renaming;
  - changing the model and effort;
  - archiving and restoring.

  Deleting a chat is reserved for the user who created it. In owner-only scopes everything stays as today.
- **The creator** stays recorded as the chat's owner.
- **The sender of each turn** is recorded on the turn. The credential resolver and the cost guard use the sender, not the chat's creator.
- **Live view:** while a turn runs, every open socket on that chat receives its frames, not only the sender's. The rule of one running turn per chat applies: a second user's `send` gets `turn_active`.
- **Access checks:** the scope permission is re-evaluated as today, per frame and during a running turn (`DCM-CHAT-10`). A user who loses scope access loses the shared chats with it.
- **Turn payloads** in shared scopes carry the sender's id, so an app can attribute turns if it wants to.
- **Documentation:** the design doc, the `CHANGELOG` and the `SHARED_CAPABILITIES` row.

## Non-goals

- Showing authors in the ui-core-micha surface.
- Per-chat sharing with chosen users.
- Changing owner-only behaviour anywhere else.

## Tier

Tier 3: authorization, the security boundary of a chat.

## Risks

- **Widened access:** a wrong shared-scope decision exposes chats to everyone with scope access. The hook is the only switch, it defaults to off, and the scope permission still applies.
- **Spend attribution:** turns are billed to the sender. A shared chat must not let one user spend another user's budget.
- **Broadcast leak:** frames must reach only sockets whose users currently pass the scope permission.

## Required tests to write

- Shared scope:
  - a second user with scope access can list, read, send, rename, change the selection, and archive or restore;
  - the second user cannot delete a chat they did not create;
  - a user without scope access sees neither the chats nor their frames.
- Owner-only scope: behaviour is unchanged. Another user gets 404 and the socket closes with 4403.
- Sender: the turn records its sender, and the credential resolver and cost guard receive the sender.
- Live view:
  - both users' sockets receive the running turn's frames;
  - a second `send` during a running turn gets `turn_active`;
  - a user whose scope access is revoked mid-turn stops receiving frames, and the socket closes with 4403.
- WebSocket inventory: `assert_all_consumers_secure` still holds.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes the seams and the shapes Part A leaves open. This is an authorization change: when in doubt, deny. Do NOT write a skeleton: every item must be fully implemented and tested. `DCM-CHAT-11` is implemented in the same run; it owns the `send`-frame context, structured turns and `completion.py`; this order owns everything about who may see and use a chat. Both edit `consumers.py` and `turns.py`: keep each other's edits.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories.

## Context package (verified against the landed code 3.2.0, paths under `src/django_core_micha/assistant/`)

- Today every access path filters on the owner: `views.py` `_chat(user, chat_id)` (`AssistantChat.objects.get(pk=chat_id, owner=user)`, then `can_access_scope`, 404 otherwise), `ChatListCreateView.get` (`filter(owner=request.user, ...)`), `ChatDetailView` (get/patch/delete all through `_chat`), `TurnsView`; `consumers.py` `IsChatOwnerWs`, `ChatConsumer.get_object` (`filter(owner=..., pk=...)`), `receive` re-check; `turns.py` `_create_turn` (`AssistantChat.objects.select_for_update().get(pk=chat_id, owner_id=user_id)`, and the concurrency counts `filter(chat__owner_id=user_id)` / `_over_limit` mine-list by `chat__owner_id`), `run_turn(chat, user, message, emit, ...)`.
- `hooks.py`: `_hook(name)`, `can_access_scope(user, scope)` (default allows only the empty scope).
- `models.py`: `AssistantChat.owner` (the creator, stays), `AssistantTurn` has no sender yet. Add `sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")` and write the migration by hand as `migrations/0003_turn_sender.py` (one `AddField`, depends on the latest existing migration, check `migrations/` for the exact name; the Orchestrator verifies with `makemigrations --check`).
- `turn_payload(turn)` in `turns.py` is called from `views.py` (TurnsView), `consumers.py` (history), `run_turn` and `_emit_final`.

## Fixed design

1. **Shared-scope hook** `DCM_ASSISTANT_SHARED_SCOPE(scope) -> bool` (optional, dotted path or callable, `hooks.py`: `is_shared_scope(scope)`). Not configured, any exception, or an empty scope: not shared (an empty scope is never shared). 
2. **One visibility helper** (new module `access.py`, used by every path, no path re-implements it): `can_view_chat(user, chat)` is true when the user passes `can_access_scope(user, chat.scope)` AND (`chat.owner_id == user.pk` OR `is_shared_scope(chat.scope)`); `is_creator(user, chat)` is `chat.owner_id == user.pk`. Scope permission is ALWAYS required, for the owner too (as today).
3. **REST:** `_chat(user, chat_id)` loads by pk only and answers 404 unless `can_view_chat`. `ChatListCreateView.get`: without `?scope` the list is the user's own chats exactly as today; with `?scope=s` (already requires `can_access_scope`, else 404) in a shared scope it lists ALL chats of that scope (any owner), in an owner-only scope only the user's own. Cursor contexts stay bound to user and scope. `ChatDetailView.patch` (title, archived, selection) works for every user who `can_view_chat`; `delete` only for `is_creator`: a viewer who is not the creator gets 403 `{"code":"forbidden","error":"forbidden"}`, a user who cannot view the chat gets 404 as today. `TurnsView` uses `_chat`. `POST chats/` unchanged (creator = owner; `scope` still needs `can_access_scope`).
4. **Sender:** `AssistantTurn.sender` is set to the user who sent the message in `_create_turn`. `_create_turn` must no longer filter the chat by owner: load by pk, require `can_view_chat(user, chat)` (raise `DriverError("access_lost")` otherwise), keep the archived / selection / `turn_active` checks. The per-user concurrency counts use the sender (`sender_id=user_id`), not `chat__owner_id`. `run_turn` already receives the sending `user` and passes it to `resolve_credential`, the cost-guard calls (drivers) and the tool handlers: keep it that way and pin it with a test (a second user's turn resolves the SECOND user's credential and reserves against the SECOND user's budget). `turn_payload(turn, chat=None)` adds `"sender_id": turn.sender_id` only when the chat's scope is shared (pass the chat from the callers to avoid per-turn queries; when `chat` is None use `turn.chat`); owner-only payloads stay byte-identical to today.
5. **Live view (WebSocket):** `IsChatOwnerWs` becomes `CanAccessChatWs` (uses `can_view_chat`; keep `IsChatOwnerWs` as an alias name for existing imports), `get_object` loads by pk and returns the chat only when `can_view_chat`. In a shared scope every socket of the chat joins the Channels group `dcm_assistant_chat_<chat pk>` in `post_connect` and leaves it in `disconnect`; the `emit` that `ChatConsumer` hands to `run_turn` publishes each frame with `channel_layer.group_send(group, {"type": "chat.frame", "frame": frame})` and every consumer's `chat_frame` handler delivers it with `send_json` ONLY after re-validating access for ITS user (session valid, `can_view_chat` on a freshly loaded chat), at most once per `access_recheck_seconds` per socket (cache the last positive result with `time.monotonic()`); on a failed check it does not deliver and closes with 4403. When the channel layer is None, or the scope is not shared, `emit` stays the plain `self.send_json` of the sender as today (owner-only behaviour unchanged, including 4403 on a foreign chat). A second `send` while a turn runs gets `{"type":"error","code":"turn_active"}` (the DB check in `_create_turn` already raises it; make sure it reaches the second sender, not the group). A `stop` frame only cancels the turn started on the same connection (document this). `history` on connect carries `turn_payload(..., chat)` rows.
6. **Docs:** `docs/design/assistant-chat.md`: the hook, the rule table (who may list, read, send, rename, re-select, archive, restore, delete), 403 `forbidden`, `sender_id`, the group delivery and the per-socket re-check. Do NOT edit `CHANGELOG.md`, `pyproject.toml`, `WORK_ORDERS.md` or `webapp-management` (the Orchestrator releases).

## Tests (new files only: `assistant/tests/test_shared_scopes.py`, `assistant/tests/test_shared_live_view.py`)

Imitate `assistant/tests/test_core.py` (REST via `APIRequestFactory` + `force_authenticate`, consumer unit tests with mocked `send_json`/`close`), `assistant/tests/test_access_recheck.py` (fake drivers through `run_turn`) and `tests/test_channel_layer.py` in the repo root `tests/` (how a channel layer is configured in tests; use `override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})` and `channels.testing.WebsocketCommunicator` or direct consumer calls). Cover every bullet of Part A "Required tests to write": shared scope with a second user (list, read, send, rename, selection, archive, restore; delete refused with 403 for a non-creator and allowed for the creator), a user without scope access sees nothing (404 REST, 4403 socket, no frames), owner-only scope unchanged (404 and 4403), sender recorded and credential resolver plus cost guard called with the sender (assert the arguments), both sockets receive the running turn's frames, a second send gets `turn_active`, a user revoked mid-turn stops receiving frames and the socket closes with 4403, `assert_all_consumers_secure(["django_core_micha.assistant.consumers"]) == []` still holds, and an empty scope is never shared even when the hook returns True. Run only your new test files with the interpreter from the dispatch note.

## Invariants
Hook off = byte-identical behaviour for cockpit and maana; the scope permission is checked on every path and every frame; no path loads a chat without `can_view_chat`; frames never reach a socket whose user fails the re-check; a non-creator can never delete; spend is attributed to the sender.

## Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.** Everything below is the Orchestrator's work after you finish.

Gate #1 recorded at django-core-micha@465767f. Tier 3 (authorization) · tests: the groups of Part A. Implemented together with `DCM-CHAT-11` in one codex/gpt-6-luna dispatch (high effort) on top of 3.2.0. Reviews include `sec_reviewer` (authorization) and the review must run on the FINAL diff including any post-review fixes (a fix after review reopens the review). Orchestrator-owned: migration check, CHANGELOG + minor version 3.3.0, `SHARED_CAPABILITIES.md` row, register rows, publish observed. Report back to local_03d1141f-9577-4c9e-8ccf-3343b3b34a19 (consumer: survey_app/SVA-AIB-45).
