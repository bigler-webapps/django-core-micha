# DCM-CHAT-4 — Chat core accepts synchronous tool handlers

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found live on staging in `cockpit/CP-CHAT-4`. Released together with `DCM-CHAT-5`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The chat core calls each registered tool handler directly inside its asyncio event loop (`assistant/drivers/base.py`, `invoke_tool`). A plain synchronous handler, which is the normal case in a Django app, therefore runs on the event loop. A handler that reads from the database fails there with `SynchronousOnlyOperation`, and the core reports that as a provider error. The design document describes the handler as `handler(user, chat, arguments, trust_class) -> (content, sources)` and does not say it has to be async. cockpit hit this and wrapped its handler in an async function (cockpit `7ddce49`).

Expected outcome: a tool handler may be either synchronous or async. The core runs a synchronous handler in a worker thread, where database access is allowed. It awaits an async handler as it does today. The design document states both forms.

## Scope

- **Handler invocation:** synchronous handlers run off the event loop in a worker thread that permits Django ORM access. Async handlers are awaited unchanged. Every other part of `invoke_tool` stays as it is: argument validation, the result shape check, the prompt-size bound and the error mapping.
- **Documentation:** `docs/design/assistant-chat.md` and the `CHANGELOG` describe both handler forms. They also state that a synchronous handler may use the ORM.

## Non-goals

- Changing the handler signature, the result shape or the tool registry setting.
- Any change in cockpit. Its async wrapper keeps working and is not removed here.
- Every other driver or turn behaviour (pre-init events and session recovery are `DCM-CHAT-5`).

## Tier

Tier 3: a change inside shared-core.

## Risks

- **Thread choice:** a worker thread that is not allowed to use the ORM would move the failure without fixing it. The test must perform a real database read inside a synchronous handler.
- **Double invocation:** a handler must still be called exactly once per tool call.

## Required tests to write

- A synchronous handler that performs a database read returns its content and sources through `invoke_tool`.
- An async handler still works unchanged.
- An exception raised inside a synchronous handler maps to the same error as today.
- A synchronous handler is called exactly once per tool call.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Implement together with `DCM-CHAT-5` and publish both as one patch release; `cockpit/CP-CHAT-4` pins that release before its promotion.
