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

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and invariants so that UCM-BILL-3 (UI) and the apps build against them. One dispatch; complete, no stubs. Money and access are involved: refunds and access ending are ONE idempotent operation, and when in doubt deny the withdrawal rather than refund twice.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories. Another implementer works in the same tree on `assistant/*` files for DCM-CHAT-13 at the same time: do not touch `assistant/`, `ai_prices.py`, `extraction/` and keep their edits. Run tests with the interpreter named in your dispatch note.

### Context package (landed 3.4.0, paths under `src/django_core_micha/billing/`)

- `models.py`: `BillingCustomer`, `StripePrice`, `Subscription` (`stripe_subscription_id`, status, period, `cancel_at_period_end`, `ended_at`), `TeamPackage` (`stripe_ref` = payment intent or session id, `starts_at`, `ends_at`, seats), `TeamSeat`, `FreeAccess`, `TokenAccount`, `TokenEntry` (append-only ledger: `kind` grant|purchase|debit|expiry|adjustment, signed `amount`, `remaining`, `expires_at`, `reference`, `app`; unique `(account, kind, reference)` where the reference is not empty), `StripeEvent`, `AICostRecord`.
- `stripe_service.py`: `process_stripe_event(event)` (handlers for `checkout.session.completed|async_payment_succeeded`, `customer.subscription.*`, `invoice.paid|payment_failed`; entitlements fixed in the checkout session metadata; period helpers `_period`, `_invoice_subscription_id`), `get_customer`, `checkout_session_params`, `_stripe()` (lazy import, sets the key), `_grant_period` (grant reference `stripe:<subscription id>:<period start>`), team package grant reference `stripe:team:<ref>`, token package purchase reference `stripe:checkout:<session id>`.
- `services.py`: `access_state`, token functions (`grant_tokens`, `credit_purchase`, `debit_tokens` (reference mandatory, idempotent, refuses a reused reference with another amount), `expire_tokens`, `token_balance`), `record_ai_call`.
- `views.py` / `urls.py`: plain Django views (CSRF enforced explicitly with `@csrf_protect`, session auth, JSON), routes `me/`, `team/seats/`, `cost-report/`, `checkout/`, `portal/`, `webhook/`.
- `docs/design/billing.md` is the contract of the module; extend it. E-mail: `django_core_micha/emails/__init__.py` has the project name, language and greeting helpers (`get_preferred_language`, `get_greeting_name`) and `email_texts.py` the existing German/English texts; the message is sent with Django's `send_mail` (look at how `render_invite_email` is used by its callers); texts German and English, language from the user.

### Fixed design

1. **Purchase record.** New model `Purchase` (migration `0002_purchase_withdrawal.py`, hand-written, tests run with migrations): `user`, `kind` (`subscription|team_package|token_package`), `plan_key`, `stripe_object_id` (subscription id, or the payment intent / session id used as `TeamPackage.stripe_ref` / purchase reference), `stripe_payment_intent_id`, `stripe_invoice_id` (subscriptions: the first invoice), `amount_paid_cents`, `currency`, `billing_country` (ISO 3166 alpha-2 from `customer_details.address.country`), `has_business_tax_id` (non-empty `customer_details.tax_ids`), `purchased_at` (payment time: the event time), `term_start`, `term_end` (the paid term; for a subscription the first period), `grant_references` (the ledger references of the allowance and purchase entries this purchase created), `tokens_granted`, `status` (`active|withdrawn`), `withdrawn_at`, `refund_cents`, `stripe_refund_id`, `withdrawal_note` (the calculation as text). Created idempotently in `process_stripe_event` where the entitlement is created (unique per `stripe_object_id`); the first payment of a subscription is the withdrawable purchase, renewals are not new purchases. Audit registration like the other models (redact `stripe_*`, `billing_country` is not secret).
2. **Eligibility** (`billing/withdrawal.py`, one pure function `eligibility(purchase, now)` returning a typed result with a reason code): eligible only when `status == "active"`, `billing_country` is in the EU/EEA list (the 27 EU members plus IS, LI, NO; keep the list in one constant, document it), `has_business_tax_id` is false, and `now - purchased_at <= 14 days` (day 14 inclusive, day 15 not). Switzerland, business and older purchases are not eligible; reasons `not_active`, `country_not_eligible`, `business_purchase`, `period_over`. A setting `DCM_BILLING_WITHDRAWAL_ENABLED` (default True) switches the function off; off means every call answers `withdrawal_disabled`.
3. **Refund calculation** (`refund_amount(purchase, now)` pure, integer cents, no floats left in money): `time_share = used_days / term_days * amount_paid_cents` (used days counted from `term_start` to `now`, whole days rounded UP, capped at the term length; token packages have no time share), `token_share = consumed / tokens_granted * amount_paid_cents` where `consumed = sum(amount - remaining)` over the purchase's `grant_references` entries (expired remainders are NOT consumed), `used = max(time_share, token_share)` rounded UP to a cent, `refund = max(0, amount_paid_cents - used)`; when `tokens_granted == 0` only the time share applies, for token packages only the token share. The result object carries both shares, the used amount and the refund so the preview can show the calculation.
4. **Preview and withdrawal endpoints** (add to `views.py`/`urls.py`, session auth, `@csrf_protect`): `GET withdrawal/` lists the user's purchases with `{id, kind, plan_key, purchased_at, amount_paid_cents, currency, eligible, reason, withdrawn_at}`; `GET withdrawal/<id>/preview/` -> `{eligible, reason, amount_paid_cents, currency, time_share_cents, token_share_cents, used_cents, refund_cents, calculation}` (`calculation` is the human readable line shown to the person); `POST withdrawal/<id>/` with `{"confirm": true}` executes. Only the purchase's own user may preview or execute (another user: 404). A purchase that is not eligible: 409 `{"code": <reason>}`. A purchase already withdrawn: 200 with the stored result (`already_withdrawn: true`), no second refund.
5. **Execution** (`withdraw(purchase, now)`; ONE transaction per step, idempotent and re-runnable after a crash): lock the purchase row (`select_for_update`); if already `withdrawn` return the stored result; compute the refund; (a) end the access: subscription -> `stripe.Subscription.cancel(id)` and the local `Subscription` row `status="canceled"`, `ended_at=now`, `current_period_end=now`; team package -> `TeamPackage.ends_at=now` and the seats stay recorded but access ends (`access_state` already follows `ends_at`); token package -> nothing to end; (b) zero the remaining tokens of the purchase's grant/purchase entries by writing `expiry`-kind ledger rows with reference `withdrawal:<purchase id>:<entry id>` (idempotent through the unique constraint); (c) refund through Stripe: `stripe.Refund.create(payment_intent=..., amount=refund_cents, metadata={"app","purchase_id"}, reason="requested_by_customer")` with an idempotency key `dcm-withdrawal:<app>:<purchase id>` so a retry never refunds twice; skip the call when the refund is 0; (d) mark the purchase `withdrawn` with `withdrawn_at`, `refund_cents`, `stripe_refund_id`, `withdrawal_note`; (e) send the confirmation e-mail (date and time, plan, amount paid, the calculation, the refund and that it is paid to the original payment method within 14 days, in the user's language; an e-mail failure is logged by class name and does not undo the withdrawal; the operation is marked so the e-mail is sent at most once). Stripe errors before the refund roll back the whole step and answer 502 `withdrawal_unavailable` without marking anything withdrawn; a cancelled subscription whose refund call failed is retried by the next POST (idempotent cancel: a 404/"already canceled" from Stripe counts as done).
6. **Webhook stays consistent:** the `customer.subscription.deleted` event that Stripe sends after our cancel and the `charge.refunded` event must not re-grant or re-activate anything: add `charge.refunded` to the handled types as a recording no-op (`status processed`, no side effect) and make sure `customer.subscription.updated/deleted` for an already withdrawn purchase never reopens access or grants tokens.
7. **Token credit-back API** (`services.py`): `reverse_debit(owner, *, reference, reason)` finds the debit of `owner` with that reference and credits the same amount back as one `adjustment` ledger entry with reference `reversal:<reference>` (non-expiring, `remaining=amount`, `purpose=reason` truncated to the column), exactly once (a second call returns the existing entry, no second credit; the unique constraint backs this), refuses an unknown reference (`ValueError("unknown_debit")`) and an empty reason, and never credits more than the debit. Apps call it themselves; there is no automatic rule.
8. **Audit:** every withdrawal and credit-back is visible in the ledger (`expiry`/`adjustment` rows with their references) and the purchase row; Stripe events reach bigler-consult through the existing event forwarding (no code here).
9. **Docs:** extend `docs/design/billing.md` (eligibility rules incl. the EU/EEA list, the refund formula with an example, the endpoints and JSON shapes, execution order and idempotency, the setting, `reverse_debit`).

### Tests (new files only)

Eligibility: EU consumer within 14 days, day 14 vs day 15, Swiss, business tax ID, already withdrawn, disabled by setting. Refund calculation: time share, token share, larger-of rule, token package, zero-token plan, cap at term, rounding up, no negative refund. Execution (Stripe mocked, no network): subscription withdrawn (cancel called once, refund called once with the idempotency key and the right amount, access `read_only` afterwards, tokens of the purchase zeroed, e-mail sent once), second POST idempotent (no second refund), crash after cancel before refund then retry (one refund), team package, token package, other user's purchase 404, Stripe failure leaves nothing withdrawn, webhook events after the withdrawal change nothing, `charge.refunded` is a no-op. Purchase record created once per checkout/first invoice (replayed event creates no duplicate). `reverse_debit`: exactly once, unknown reference, amount equals the debit, idempotent second call. Existing billing tests stay green.

### Invariants

One idempotent withdrawal per purchase; no refund without ending access in the same operation; no refund above the paid amount; no secret or payload in logs or responses; integer cents; CSRF enforced; nothing outside the billing app changes behaviour.

### Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator 2026-10-16 (in the Maana Expertenchat, session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Start after DCM-BILL-1 has landed. Tier 3: all lenses
plus `sec_reviewer`. Release and consumer pin bumps per the dcm publish flow.
