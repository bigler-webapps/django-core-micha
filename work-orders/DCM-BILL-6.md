# DCM-BILL-6 — Token origins, team pool and seat payer in the account view

## Part A — Envelope (everyone)

**Tier:** 3 — a change inside shared-core (`django-core-micha`), billing.
**Repository:** `bigler-webapps/django-core-micha`, trunk `main`; released as a minor version.
**Precondition:** DCM-BILL-5 released (3.7.0).

### Goal and expected outcome

The operator-approved prototype of Maana's account section (`maana/work-orders/assets/MAANA-APP-8-subscription.html`,
2026-10-17) shows the token balance by origin (Kontingent, Paket, Team-Pool, Gutschrift, Test-Guthaben, each with
remaining amount and validity) and tells a seat holder whose team package the seat belongs to. The account view
`me/` cannot deliver that today: every grant is labelled `plan` (also operator credits and the sign-up trial
grant), the team pool is not part of `me/`, and `seat` carries no payer. Expected outcome: `me/` carries these
three facts additively, so the shared account section (UCM-BILL-6) can show them for every app.

### Settled decisions (operator, 2026-10-17)

1. **Origin per grant:** every row of `tokens.grants` gains `origin`: `plan` (allowance of a subscription or
   team package, purpose `plan:<key>`), `package` (purchased package), `trial` (purpose `trial`), `credit`
   (every other grant, such as an operator credit). The existing `source` field stays unchanged.
2. **Team pool:** for a team package payer and for a seat holder, `tokens.pool` carries the active pool of that
   team package: plan name, amount granted, remaining, expiry. Absent for everybody else. The personal
   `tokens.balance` stays the personal balance.
3. **Seat payer:** `seat` gains `payer_name`: the package owner's full name, or the e-mail address when no name
   is set.
4. **Additive only:** no existing key changes meaning or disappears.

### Scope and acceptance

The three additions to `me/`, the contract section in `docs/design/billing.md`, a CHANGELOG entry.

### Non-goals and do-not-touch

How tokens are spent or which account pays (apps decide that, e.g. Maana debits the pool first), the operator
endpoints, the withdrawal endpoints (they already list every purchase with `eligible` and `reason`).

### Risks

`tokens.pool` exposes the team pool's remaining amount to every seat holder; that is intended (the prototype
shows it to members). `payer_name` shows the payer's e-mail to seat holders when no name is set; the payer
assigned the seat, so the holder already knows them.

### Required tests to write or update

`origin` for each grant kind and purpose (plan allowance, team allowance, package, trial, operator credit);
`tokens.pool` for payer, for seat holder, absent for a person without a team and after the package ended;
`seat.payer_name` with and without a full name; existing `me/` keys unchanged (regression).

## Part B — Implementation map (implementer; Orchestrator completes before dispatch)

Placeholder — dispatch-blocking.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator 2026-10-17 („UCM-BILL-6 machen wir noch", in the Maana Expertenchat, session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce); this order supplies the data UCM-BILL-6 needs. Seams:
`billing/views.py::me` (grants list, team, seat), `TeamPackage` pool account, `TokenEntry.purpose`. Tier 3: all
lenses plus `sec_reviewer` (who may see the pool and the payer's name).
