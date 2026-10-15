# DCM-CHAT-7 — A provider timeout before init also discards a resumed CLI session

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-5` (released in 3.0.1), a known gap reported at its landing.

# A. Envelope — addressed to everyone

## Goal and expected outcome

`DCM-CHAT-5` made the core discard a resumed Claude Code session that fails before the CLI's `init` event, so that the next turn starts fresh from the stored history. One path escapes this. The turn runner wraps the whole driver stream in its own provider timeout (`assistant/turns.py`, `run_turn`). When that outer timeout fires, the exception it raises carries no information on whether the CLI reached `init`. The `TimeoutError` branch therefore never discards the session, and a chat whose resumed session hangs before `init` times out on every later turn.

Expected outcome: a turn on a resumed Claude Code session that ends in a provider timeout before `init` discards the session, exactly like every other pre-init failure. A timeout after `init` keeps the session as today.

## Scope

- The turn runner learns, for a timed-out turn, whether the Claude Code driver had seen `init`. On a resumed session without `init`, it clears the chat's provider session reference. The turn itself still ends as failed with `timeout`, without a retry.
- The `CHANGELOG` names the change.

## Non-goals

- Changing the timeout values or the timeout taxonomy.
- The API drivers, which keep no provider session.
- Retrying a timed-out turn.
- The pre-init event rule from `DCM-CHAT-5`.

## Tier

Tier 3: a change inside shared-core.

## Risks

- **Two timeouts:** the driver and the turn runner each enforce a provider timeout. A fix that covers only one of them leaves the gap open. The test must drive the outer one.

## Required tests to write

- A resumed session whose stream produces nothing until the turn-level provider timeout fires: the turn ends failed with `timeout`, and the chat's provider session reference is cleared.
- The same timeout after a valid `init`: the session reference is kept.
- A fresh, non-resumed session that times out before `init`: behaviour as today.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Same patch release as `DCM-CHAT-8`.
