# DCM-CHAT-6 — Chat list shows active chats as active, archived as archived

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found live on staging in `cockpit/CP-CHAT-4`. Released together with `DCM-CHAT-4` and `DCM-CHAT-5`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The chat list endpoint filters by archive state the wrong way round (`assistant/views.py`, `ChatListCreateView.get`: `archived_at__isnull=archived`). The default list returns the archived chats, and the archived filter returns the active ones. On cockpit staging, all eight chats had no archive date, yet after a reload the active list was empty and the "Archived" entry showed eight. The ucm tests use a mocked API, so nothing caught it.

Expected outcome: the default list returns exactly the owner's non-archived chats, and the archived filter returns exactly the owner's archived chats. Cursor pagination works within each list.

## Scope

- Correct the archive filter of the chat list.
- No change to the response shape, the cursor format, ownership or any other endpoint.

## Non-goals

- Other list behaviour (ordering, page size), the archive and restore actions themselves, and ui-core-micha.

## Tier

Tier 3: a change inside shared-core.

## Risks

- **Cursor context:** the cursor carries the archive flag in its context. A cursor from one list must not page into the other. The existing behaviour stays.

## Required tests to write

- With one active and one archived chat of the same owner: the default list returns only the active one, and the archived filter returns only the archived one.
- Another user's chats appear in neither list.
- Pagination: with more active chats than one page, following the cursor stays within the active list.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Same patch release as `DCM-CHAT-4` and `DCM-CHAT-5`. Live check after publishing, on cockpit staging via `cockpit/CP-CHAT-6`: after a page reload the active chats are in the list and "Archived" counts only archived chats.
