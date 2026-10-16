# DCM-BILL-3 — EU withdrawal function and token credit-back

## Part A — Envelope (everyone)

**Tier:** 3 — refunds of money and changes to access and token balances. **Repository:**
`bigler-webapps/django-core-micha`. **Precondition:** DCM-BILL-1 has landed. **UI side:** UCM-BILL-3.
**Consumers:** MAANA-APP-8 (and later apps that sell to consumers).

### Goal and expected outcome

Since 19 June 2026, sellers of online contracts to consumers in the EU must offer a withdrawal
function („Vertrag hier widerrufen") throughout the 14-day withdrawal period (Directive 2011/83/EU as
amended by Directive (EU) 2023/2673, Art. 11a). The legal texts drafted for bigler-consult
(`bigler-consult/docs/legal/kuendigung-rueckerstattung.md`) promise this function and also that
tokens of an action that failed because of a provider fault are credited back. The billing core must
make both possible for every app.

Expected outcome: an eligible person can withdraw with one action within 14 days, gets a
confirmation and a proportional refund through Stripe; apps can credit back the tokens of a failed
action exactly once.

### Settled decisions (operator, 2026-10-16)

1. **Eligibility:** the purchase's billing country (from Stripe Checkout) is in the EU or EEA, no
   business tax ID was given at checkout, and the purchase is at most 14 days old. Applies to
   subscriptions, team packages and token packages. Switzerland and business purchases are not
   eligible.
2. **Withdrawal action:** one server action per purchase; it records date and time, ends the access
   granted by that purchase immediately (read-only rules of DCM-BILL-1 apply), stops renewal, and
   sends a confirmation e-mail with the receipt of the withdrawal.
3. **Refund:** paid amount minus the larger of (a) the time share used (days used ÷ days of the term ×
   price) and (b) the token share used (tokens consumed from that purchase's grant ÷ tokens granted ×
   price); for token packages only (b). Refunded through Stripe on the original payment method within
   14 days; amount and calculation are shown to the person before confirming and in the e-mail.
4. **Token credit-back:** a debit can be reversed exactly once by reference (the action's id) with a
   reason; the reversal is recorded in the ledger. Apps use it when an action failed because of a
   provider fault, a timeout or an internal error; the core offers no automatic rule beyond the API.
5. **Audit:** withdrawals and credit-backs appear in the ledger and in the Stripe events forwarded to
   bigler-consult (BCO-BILL-1).

### Scope and acceptance

Eligibility check, withdrawal endpoint with preview of the refund and confirmation, Stripe refund,
access end, renewal stop, confirmation e-mail, ledger reversal API; tests.

### Non-goals and do-not-touch

The UI (UCM-BILL-3), app-specific rules for when to credit back (apps), Swiss or business
withdrawals, partial refunds outside the withdrawal period (manual by the operator in Stripe).

### Risks

Refunding twice or refunding without ending access loses money; withdrawing and refunding must be
one idempotent operation. Whether Stripe, as merchant of record, already provides the legally
required withdrawal function is not confirmed; this order provides it on our side regardless — if
Stripe confirms it covers the obligation, the operator may decide to switch it off (setting).

### Required tests to write or update

Eligibility (EU consumer within 14 days, Swiss, business tax ID, day 15); refund calculation for time
share, token share and package; idempotent withdrawal; access ended and renewal stopped; e-mail sent;
Stripe refund called once (mocked); credit-back exactly once by reference.

## Part B — Implementation map (implementer; Orchestrator completes before dispatch)

Placeholder — dispatch-blocking.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator 2026-10-16 (in the Maana Expertenchat, session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Start after DCM-BILL-1 has landed. Tier 3: all lenses
plus `sec_reviewer`. Release and consumer pin bumps per the dcm publish flow.
