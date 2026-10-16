# DCM-BILL-5 — Billing data for operators and a richer account view

## Part A — Envelope (everyone)

**Tier:** 3 — new endpoints exposing billing data of other accounts to the operator, and a write
action (free access). **Repository:** `bigler-webapps/django-core-micha`. **Precondition:**
DCM-BILL-1 to DCM-BILL-4 landed (3.6.1). **UI side:** UCM-BILL-2.

### Goal and expected outcome

Today the operator can grant free access only with a management command, sees subscribers and AI
costs only in Stripe or through the monthly cost report for bigler-consult, and a person sees in
„Abo" only access state, token balance and plans. The operator needs an overview per app and a switch
per account; a person needs to see their plan, term, token grants with expiry and, as team payer, who
holds a seat.

Expected outcome: three server capabilities the UI of UCM-BILL-2 builds on.

### Settled decisions (operator, 2026-10-16/17)

1. **Who is an operator:** exactly the accounts allowed to use the shared Users tab today (the same
   permission check, no new role). Every operator endpoint refuses all other accounts (403) and never
   exposes another app's data.
2. **Operator overview per app and month** (`GET operator/overview/?month=YYYY-MM`): active accounts
   by access source (subscription per plan, team seat, free access), new and ended subscriptions in
   the month, team packages and seats used, token packages sold, tokens granted / used / expired, AI
   cost in USD from the recorded AI calls (by provider and model), and the cost per used token.
   Aggregates only.
3. **Operator account list** (`GET operator/accounts/?q=&page=`): per account email, name, access
   status and source, plan, term end, token balance, free access (with reason, granted by, since).
   Paginated and searchable; read-only.
4. **Free access switch** (`POST operator/accounts/<user_id>/free-access/` `{reason}` to grant,
   `DELETE` to revoke): same rules as the existing commands (`billing_grant_free_access` /
   `billing_revoke_free_access`), reason mandatory on grant, recorded with the operator and time in
   the audit log; revoking never deletes data (read-only rules apply).
5. **Richer `me/`** (the person's own data only): `subscription` (plan key and name, billing period,
   current term end, renews yes/no, cancel at period end yes/no), `tokens.grants` (per grant: source
   — plan allowance or package —, amount, remaining, expires at or null), and for a team payer
   `team` (package plan, term end, seats total, seats used, seat holders with name and email). Seat
   holders see only that they hold a seat in a team package and until when, not the other holders.
6. **Privacy:** no Stripe secrets, raw payloads or card data in any response; emails only to
   operators and to the team payer for their own seat holders.

### Scope and acceptance

The three operator endpoints, the richer `me/`, permission checks, audit entries, pagination and
query limits, documentation in `docs/design/billing.md`; existing endpoints keep their contract.

### Non-goals and do-not-touch

The UI (UCM-BILL-2), changing subscriptions or refunds from the operator view (Stripe dashboard),
cross-app overviews (bigler-consult does that, BCO-BILL-1), deletion.

### Risks

An operator endpoint reachable by a normal account would expose all customers' billing data; every
endpoint is permission-tested. The account list must not become an N+1 query per account.

### Required tests to write or update

403 for non-operators on all operator endpoints; overview aggregates for a seeded month (each access
source, packages, tokens, AI cost); account list search, pagination, query count constant; free
access grant needs a reason, is audited, revoke keeps data read-only; `me/` shows subscription,
grants with expiry and, for a team payer only, seat holders; a seat holder does not see other holders;
no secret or raw payload in any response.

## Part B — Implementation map (implementer; Orchestrator completes before dispatch)

Placeholder — dispatch-blocking.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator 2026-10-17 (in the Maana Expertenchat, session local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Seams: `billing/views.py` (`me`, `team_seats`), `billing/services.py`
(`access_state`, `token_balance`, grants), `billing/models.py` (FreeAccess, TeamPackage, TeamSeat,
token ledger, AI call records), the free-access management commands, the permission used by the
shared Users tab. Tier 3: all lenses plus `sec_reviewer`. Release per the dcm publish flow.
