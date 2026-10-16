# DCM-RCPT-2 — Receipt reading sees the request context, and the fallback currency is per app

Prepared: 2026-10-17. Target repository: `django-core-micha` (`main`). Correction to `DCM-RCPT-1` (3.6.0), found at the acceptance of `reimbursements/RMB-RCPT-1`. Consumer: `reimbursements/RMB-RCPT-2`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

Two properties of the receipt module put wrong money values into a multi-currency app such as reimbursements:
1. **No per-request hint:** the app's extension prompt is static (`DCM_RECEIPTS_EXTENSION`). A reading through the REST endpoint therefore cannot tell the model the target currency of the claim being filled. The server-side reads in reimbursements do tell it. On a receipt that prints both a foreign amount and a CHF amount, the model can put the wrong one into the original amount.
2. **CHF fallback:** an unreadable currency becomes `CHF` for every app (`DCM-RCPT-1`'s core schema, "default CHF"). That suits a CHF-only app (spesix). In an app with foreign-currency travel, a silently set currency produces a wrong amount.

Expected outcome:
- The extension prompt can use the request's context, after the app's validation.
- The fallback currency is an app setting, including "none", where an unreadable currency stays empty.
- Apps that set nothing behave as in 3.6.0.

## Operator decisions this Envelope records (2026-10-17)

- Both corrections go into the core, not into reimbursements alone.
- spesix keeps CHF as its fallback.

## Scope

- **Context in the extension prompt:**
  - The app's extension can supply its prompt text per reading. It receives the request's context after the app's own validation of it, and the requesting user.
  - A context the validation refuses ends the reading with `invalid_request`, before any provider call.
  - The static form of `DCM_RECEIPTS_EXTENSION` keeps working.
- **Fallback currency:**
  - A setting names the currency used when the receipt's currency is unreadable.
  - The default stays `CHF` (3.6.0 behaviour).
  - A value of "none" leaves the currency empty.
- **Documentation:** `docs/design/receipts.md` and the `CHANGELOG`.

## Non-goals

- Other schema or normaliser changes.
- Changes in ui-core-micha. Its capture card already sends a host-chosen `context`.

## Tier

Tier 3: a change inside shared-core on a money path that adds a client-influenced input to a provider prompt.

## Risks

- **Prompt injection through the context:** the context is user-supplied. The app's validation is the only control. The extension decides which validated values it puts into the prompt, never the raw context.

## Required tests to write

- Context:
  - an extension that uses the validated context gets its text into the prompt of that reading only;
  - a refused context ends the reading with `invalid_request` and no provider call;
  - the static extension still works.
- Fallback currency:
  - default `CHF` unchanged;
  - a set fallback currency is used;
  - "none" leaves the currency empty.

# B. Implementation map — addressed to the implementer

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Patch or minor release; `reimbursements/RMB-RCPT-2` pins it.
