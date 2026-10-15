# DCM-CHAT-8 — API driver failures are logged on the server

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found while diagnosing `cockpit/CP-CHAT-5`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The OpenAI and Anthropic drivers of the chat core turn every provider exception into `DriverError("provider_error")` (`assistant/drivers/openai_api.py`, `assistant/drivers/anthropic_api.py`). The turn runner does not log `DriverError` (`assistant/turns.py`, `run_turn`). A provider rejection, such as an HTTP 400 for an invalid tool schema, therefore leaves no trace on the server. The user sees "The answer could not be completed", and the operator has nothing to diagnose from. In `cockpit/CP-CHAT-5` the cause could only be found by reproducing the request by hand. survey_app learned the same lesson with its CLI motor: before AIB-7, stderr was discarded.

Expected outcome: every failed provider call in an API driver writes one server-side log entry. The entry holds the driver, the model, the HTTP status, the provider's error type and code, the offending parameter when the provider names one, and the provider's error message. The client keeps receiving only the generic error code.

## Scope

- **Logging:** one warning-level log entry per failed provider call in the OpenAI and Anthropic drivers, with the fields above.
- **Redaction:** before logging, any API-key-like token in the message is redacted (`sk-…` and the Anthropic key form).
- **Unchanged:**
  - the client-facing error codes and the WebSocket frames;
  - the quota mapping (429 to `quota_exceeded`);
  - the Claude Code CLI driver, which already logs its stderr tail.

## Non-goals

- Logging request or response bodies, prompts, tool content or knowledge-base text. Only the provider's error description is logged.
- Showing provider messages to the user.
- Metrics or alerting.

## Tier

Tier 3: a change inside shared-core, touching what is written to logs.

## Risks

- **Data in logs:** provider error messages can quote parts of the request, for example a parameter value. Logging only the error object, never the request, keeps that bounded. Redacting key-like tokens covers the case where a provider echoes a key prefix.

## Required tests to write

- An OpenAI `APIStatusError`-like failure with status 400 and an error body writes one log entry containing status, type, code, param and message. The client still receives `provider_error`.
- The same for the Anthropic driver.
- A key-like token inside the provider message appears redacted in the log entry.
- A 429 still maps to `quota_exceeded`.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Same patch release as `DCM-CHAT-7`.
