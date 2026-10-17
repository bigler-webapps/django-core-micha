# DCM-CHAT-14 — Aborted assistant turns are charged, not released (closes S434)

# A. Envelope

**Goal.** Close S434 from the 2026-10-17 estate audit (`webapp-management/SECURITY_FINDINGS.md`). Expected
outcome: stopping or timing out an assistant turn after the provider call has started no longer frees the
cost reservation; the daily cap counts the turn and the cost overview shows it.

**Background (from the finding).** In the paid API drivers (`assistant/drivers/anthropic_api.py`,
`openai_api.py`, base in `drivers/base.py`) `except BaseException: release_reservation(...)` also catches the
task cancellation from a `stop` frame (`assistant/consumers.py`) and provider timeouts. Usage is set only
after the final message, so an aborted turn writes no `AICostRecord` while the partial answer is still
stored. The extraction side already keeps ambiguous reservations until they expire. A user sends a large
prompt, reads the deltas and stops before the end: every turn is admitted against the cap and then
released, so the daily counter never grows.

**Scope.**
1. A reservation is released only when the provider call has not started. Once it has started, an aborted
   turn (stop, cancellation, timeout, provider error) is charged conservatively (measured usage where known,
   otherwise the reserved amount) and written as an `AICostRecord`, consistent with how extraction treats
   ambiguous calls.
2. The reservation uses the turn's real `max_tokens`.
3. Minor release with a CHANGELOG entry.

**Non-goals / do not touch.** The caps and limits themselves; frame and concurrency limits; extraction; app
pin bumps (separate).

**Risks.** Charging users for genuine provider failures after the call started (accepted: conservative);
turns that fail before anything is sent must stay free.

**Required tests (write as part of the implementation).**
- `stop` after the provider call started → reservation not released, cost record written, cap counter
  increased.
- Failure before the provider call → released, no record.
- Timeout mid-stream → charged.
- Normal completion → actual usage as today.
- The reservation amount follows the turn's `max_tokens`.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (shared core, cost control).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

_To be filled by the Orchestrator (context package, preamble, progress contract) before dispatch._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S434; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
