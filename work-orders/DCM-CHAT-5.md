# DCM-CHAT-5 — A chat survives an interrupted Claude Code turn

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`). Correction to `DCM-CHAT-1`, found live on staging in `cockpit/CP-CHAT-4`. Released together with `DCM-CHAT-4`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

The Claude Code CLI driver treats every event before the CLI's `init` event as a security violation (`assistant/drivers/claude_cli.py`, the `elif not saw_init` branch). After a turn is interrupted, the CLI (verified with version 2.1.292) emits `{"type":"system","subtype":"ui_invalidate"}` before `init` when the session is resumed. Three things interrupt a turn: the user pressing Stop, a dropped socket, or a backend restart. Every later turn in that chat then fails with `security_violation`. The chat is permanently unusable, while a new chat works. Stop is a primary control of the chat surface, so this hits ordinary use.

Expected outcome: a chat keeps working after an interrupted turn. The check that the CLI offers exactly the registered tools stays as strict as it is. A chat whose resumed provider session fails before `init` for any other reason recovers on its next turn, from its stored history.

## Operator decisions this Envelope records (2026-10-15)

- Tolerate benign pre-init system events **and** add self-healing for resumed sessions.
- Released together with `DCM-CHAT-4` as one patch.

## Scope

- **Events before `init`:**
  - An event with `type` `system` and any subtype other than `init` is ignored. It is not forwarded and not treated as an error.
  - Any event of another type before `init` still ends the turn with `security_violation`.
  - The `init` check itself (exact tool list, exact MCP server set) is unchanged.
  - Behaviour after `init` is unchanged.
- **Self-healing:** a turn that resumed an existing provider session and fails before the `init` event, with any error, makes the core discard the chat's provider session reference.
  - The next turn in that chat then starts a fresh provider session, with the history rebuilt from the stored turns. This is the same path a driver switch already takes.
  - The failed turn itself still ends as failed with its error. There is no automatic retry inside the same turn.
- **Documentation:** the design document and the `CHANGELOG` describe both behaviours.

## Non-goals

- Loosening the `init` check, or accepting any non-system event before it.
- Retrying a failed turn automatically.
- Cleaning up old session files on disk.
- The OpenAI and Anthropic API drivers.
- Changes in cockpit, apart from the later pin bump that `CP-CHAT-4` does itself.

## Tier

Tier 3: a change inside shared-core that touches a security check.

## Risks

- **Weakening the security check:** ignoring events before `init` must not let model output or tool activity through unchecked. Only `system` events are ignored, and nothing reaches the client before a valid `init`.
- **A genuine security violation on resume:** if the init check fails because the tools really differ, discarding the session only moves the failure to a fresh session, which runs the same check. Nothing is gained by bypassing it, and nothing is lost.
- **CLI drift:** later CLI versions may emit other system subtypes before `init`. The rule covers them by type, not by a list of subtypes.

## Required tests to write

- A stream with `{"type":"system","subtype":"ui_invalidate"}` before a valid `init` completes the turn normally.
- A non-system event (for example an `assistant` event) before `init` still ends with `security_violation`.
- A wrong `init` (extra tool) still ends with `security_violation`, also when a system event came first.
- A resumed session failing before `init`:
  - clears the chat's provider session reference;
  - the next turn starts without resume and with the history rebuilt from the stored turns.
- A fresh, non-resumed session failing before `init` leaves the chat as it is today, because there is nothing to discard.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Implement together with `DCM-CHAT-4` and publish both as one patch release. Live check after publishing, on cockpit staging through `CP-CHAT-4`: stop a running Claude Code turn, then send a further message in the same chat; it must answer.
