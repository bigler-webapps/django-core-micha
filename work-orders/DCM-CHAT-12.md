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

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Same minor release as `DCM-CHAT-11`. Reviews include `sec_reviewer` (authorization).
