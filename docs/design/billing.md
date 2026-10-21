# Shared billing contract

`django_core_micha.billing` is opt-in. An application installs the app and mounts `billing.urls` at its chosen API prefix; applications that do not install it receive no billing tables or routes. bigler-consult is the seller and uses one Stripe account with Managed Payments. Stripe Checkout and the Stripe customer portal are the only payment flows in this contract.

## Configuration

Settings are read with `getattr(settings, name, default)`. Configure `DCM_BILLING_APP` to the application key, `DCM_BILLING_FILE` to its UTF-8 `billing.yaml` (default: `BASE_DIR / "billing.yaml"`), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `DCM_BILLING_SUCCESS_URL`, `DCM_BILLING_CANCEL_URL`, and `DCM_BILLING_PORTAL_RETURN_URL`; optionally `DCM_BILLING_CHECKOUT_CONTEXTS` for contextual checkout (DCM-AUTH-12, below). The cost report accepts a bearer token whose lowercase SHA-256 digest is in `DCM_BILLING_REPORT_TOKEN_SHA256`. Secret values must not be logged or returned.

Each application owns one YAML file with exactly `app`, `currency`, `plans`, and `token_rates`. `app` must match `DCM_BILLING_APP`; v1 supports `usd`. Plan keys match `[a-z0-9_]{1,40}` and are unique. Subscription plans require `interval: month|year`; team packages require positive `term_months` and `seats`; token packages require positive `tokens`. All plans require a name, positive `amount_cents`, and Stripe `tax_code`. Subscription and team plans may set a positive `token_allowance` (omitted means zero). Token rates map names to positive integer tokens per unit. `billing_check` and the Django system check use the same loader and validation.

Example:

```yaml
app: maana
currency: usd
plans:
  - key: personal_monthly
    kind: subscription
    name: Personal monthly
    amount_cents: 1500
    interval: month
    token_allowance: 100000
    tax_code: txcd_10103001
  - key: team_6m_5
    kind: team_package
    name: Team, 5 seats, 6 months
    amount_cents: 60000
    term_months: 6
    seats: 5
    token_allowance: 600000
    tax_code: txcd_10103001
  - key: tokens_100k
    kind: token_package
    name: 100k tokens
    amount_cents: 1000
    tokens: 100000
    tax_code: txcd_10103001
token_rates:
  chat_1k_input_tokens: 10
  chat_1k_output_tokens: 40
```

Use Stripe test credentials in local and staging settings and live credentials only in production. Provision secrets through the consuming application's normal secret flow.

## Stripe plan sync and payments

Run `billing_sync_plans [--dry-run]`. Each plan has a Stripe Product with metadata `app` and `plan_key`, and a Price with lookup key `<app>:<plan_key>:v<version>`. The fingerprint covers amount, currency, interval, kind, and tax code. An unchanged fingerprint makes no Stripe call. A changed fingerprint creates a new Price version; old Prices are not updated or deleted, so existing subscriptions retain their Price. Dry run makes no Stripe calls.

`POST <prefix>/checkout/` requires a session-authenticated user and JSON `{"plan_key":"personal_monthly"}`; it returns `{"url":"https://..."}`. Subscription plans use Checkout `mode=subscription`; team and token packages use `mode=payment`. The session and, for subscriptions, subscription metadata include `app`, `plan_key`, and `user_id`. Checkout explicitly enables Stripe Managed Payments, requires billing address collection, and enables Stripe automatic tax. Stripe's tax code is attached to the Product. Checkout uses the configured success and cancel URLs. The `managed_payments.enabled` parameter is present in the installed Stripe 16 `SessionCreateParams` definition.

A request with a `context` key is a contextual checkout instead (next section); a request without it, or with `context: null`, is handled exactly as described here, including its response shape and destinations.

`POST <prefix>/portal/` requires a session-authenticated user and returns `{"url":"https://..."}`. It returns 404 `{"detail":"no_customer"}` when the user has no Stripe customer. Cancellation, payment methods, and receipts are handled in Stripe's portal using `DCM_BILLING_PORTAL_RETURN_URL`.

## Webhook and reconciliation

`POST <prefix>/webhook/` is CSRF-exempt and has no session authentication. It requires `Stripe-Signature`, verifies the raw body with Stripe's default signature tolerance, and returns an empty 400 on invalid signatures or payloads. The event id is stored uniquely before processing. Duplicate ids return 200 without running side effects. Foreign-app events and unknown event types are stored as `ignored`. A processing error rolls back effects, stores only its exception class, and returns 500 so Stripe retries. Signature headers and payloads are never logged.

| Stripe event | Billing effect |
| --- | --- |
| `checkout.session.completed` (subscription) | Retrieve and upsert the subscription; grant its current term allowance idempotently. |
| `checkout.session.completed` (team package) | Create the prepaid package and team token grant once. |
| `checkout.session.completed` (token package) | Credit the purchased token amount once. |
| `customer.subscription.created` / `.updated` | Upsert status and period; active, trialing, and past-due periods grant access and allowance. |
| `customer.subscription.deleted` | Upsert cancellation/end time; access ends according to the period end. |
| `invoice.paid` | Retrieve and upsert the subscription; grant the new period allowance once per period. |
| `invoice.payment_failed` | Mark the known subscription past due; access remains during its current period. |

| `checkout.session.async_payment_succeeded` | Same effect as a paid `checkout.session.completed`. |
| `checkout.session.async_payment_failed` / `checkout.session.expired` | Only a contextual checkout attempt changes status (next section); otherwise ignored. |

Every event is checked against `metadata.app`; invoice processing also looks at invoice line metadata or retrieves its subscription when necessary. `billing_reconcile [--since ISO_TIMESTAMP]` (default seven days ago) lists subscriptions and recent events, filters by app metadata client-side, and applies the same idempotent handlers. Reconciliation failures report an exception class, not provider data.

## Contextual checkout and attempt resolution (DCM-AUTH-12)

A contextual checkout lets an application send a person to Stripe Checkout from a specific flow (for example a first-use setup) and find that flow again when the person returns, without trusting the return itself. It is opt-in per request: nothing changes for a request without `context`.

**Configuration.** `DCM_BILLING_CHECKOUT_CONTEXTS` maps context keys (`[a-z0-9_]{1,40}`) to exactly `{"success_url": ..., "cancel_url": ...}`; both are absolute `http` or `https` URLs with a host and must not already carry a `checkout_attempt` query parameter. Absent or empty means no context is accepted. A malformed value is the system check error `dcm_billing.E002`, and at runtime no context is accepted. Destinations come only from this setting; the browser never supplies a return URL.

```python
DCM_BILLING_CHECKOUT_CONTEXTS = {
    "setup": {"success_url": "https://maana.example/setup/plan/done", "cancel_url": "https://maana.example/setup/plan"},
    "account": {"success_url": "https://maana.example/account/billing/done", "cancel_url": "https://maana.example/account/billing"},
}
```

**Migration.** `dcm_billing.0003_checkoutattempt` adds one table (`CheckoutAttempt`) and changes no existing billing table or row.

**Starting a checkout.** `POST <prefix>/checkout/` (session-authenticated, CSRF-protected) with

```json
{"plan_key": "personal_monthly", "context": "setup", "idempotency_key": "6f1c0e9a-3b6d-4f7e-9d7a-2a5b8c1d0e4f"}
```

`idempotency_key` is generated by the client for one deliberate checkout (a click on "pay"), matches `[A-Za-z0-9_-]{16,64}`, and is reused unchanged when the same request is retried after a network interruption. A new key is a new, explicit attempt. The server creates an attempt bound to the signed-in person, `DCM_BILLING_APP`, the plan and the context, with an opaque server-generated `reference` (`[A-Za-z0-9_-]`, 16 to 64 characters, unguessable). It creates one Stripe Checkout Session for it with the context's destinations, each carrying the added query parameter `checkout_attempt=<reference>` (existing query parameters are kept), `client_reference_id` = reference, and metadata `checkout_attempt` and `checkout_context` next to the existing `app`, `plan_key` and `user_id`. Everything else (mode, Managed Payments, tax, address collection, plan metadata) is the same as for a checkout without context. The Stripe request uses the idempotency key `dcm-checkout:<app>:<reference>` and the price stored on the attempt, so a retry can never produce a second session.

Response 200:

```json
{
  "url": "https://checkout.stripe.com/c/pay/cs_test_...",
  "attempt": {
    "reference": "Qm9sZC1yZWYtZXhhbXBsZS0xMjM0NTY",
    "context": "setup",
    "plan_key": "personal_monthly",
    "status": "open",
    "created_at": "2026-10-21T09:15:02.120000+00:00",
    "expires_at": "2026-10-22T09:15:03+00:00"
  }
}
```

Replaying the same `idempotency_key` with the same plan and context returns the same attempt and the same `url` without a second Stripe session; if an earlier call failed before the session was stored, the replay repeats the identical Stripe request. Errors write no attempt unless stated:

| Status | Body | When |
| --- | --- | --- |
| 401 | `{"detail": "authentication_required"}` | not signed in |
| 400 | `{"detail": "unknown_context"}` | `context` is not a configured key (also when none is configured) |
| 400 | `{"detail": "invalid_idempotency_key"}` | key missing or malformed |
| 400 | `{"detail": "unknown_plan"}` | plan not declared or not synced to Stripe |
| 409 | `{"detail": "idempotency_key_reused"}` | the key belongs to an attempt with another plan or context |
| 409 | `{"detail": "attempt_not_open", "attempt": {...}}` | the key's attempt is no longer open (paid, processing, failed or expired); start a new attempt with a new key |
| 502 | `{"detail": "checkout_unavailable"}` | Stripe failed; the attempt is kept without a session and a replay with the same key retries the identical Stripe request |

**Resolving a return.** `GET <prefix>/checkout/attempts/<reference>/` (session-authenticated, `no-store`) answers 200 with the same `{"url", "attempt"}` shape. It answers 404 `{"detail": "attempt_not_found"}` for an unknown or malformed reference, for another person's attempt and for another app's attempt alike, and 401 when not signed in. `url` is the Stripe Checkout URL while the attempt is `open` and its session has not expired, so the person can resume the same payment after a cancellation; otherwise `null`. The application reads `context` to return to the originating flow; the `checkout_attempt` parameter, the destination used and any other query parameter are never evidence of payment.

**Status.** Only verified Stripe events processed by the webhook or `billing_reconcile` change it, inside the same transaction as the existing grant logic; the browser return, the success destination or an added query parameter never do.

| `status` | Meaning | Changed by |
| --- | --- | --- |
| `open` | No verified completion yet. After a success return the webhook may still be in flight: keep resolving. After a cancellation nothing was paid; `url` resumes the same session. | creation |
| `processing` | Checkout completed, payment not yet settled (delayed payment method). No entitlement yet. | `checkout.session.completed` with `payment_status: unpaid` |
| `paid` | Payment verified; the plan's existing grant (subscription, team package or token credit) was applied in the same transaction. | `checkout.session.completed` with `paid` or `no_payment_required`, `checkout.session.async_payment_succeeded` |
| `failed` | The delayed payment failed. Retry with a new key. | `checkout.session.async_payment_failed` |
| `expired` | The Checkout Session expired unpaid. Retry with a new key. Also reported for an `open` attempt whose session `expires_at` has passed. | `checkout.session.expired` |

Transitions only move forward: `open` to any other status, `processing` to `paid` or `failed`; `paid`, `failed` and `expired` are final, so a late or repeated event never moves an attempt back. Duplicate events, delayed events and reconciliation keep the existing idempotent grant behaviour: one grant per payment, whichever path runs first. An event finds its attempt by the session id; if the session id was not stored yet, by `metadata.checkout_attempt` together with matching `app` and `user_id`, which then stores it. Entitlement itself is still read from `GET <prefix>/me/` (`access`, `tokens`); an attempt never grants access on its own. The webhook endpoint of an application that uses contextual checkout must subscribe `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed` and `checkout.session.expired`.

**Limits.** A new attempt does not close an older open one: two open attempts are two payable sessions until Stripe expires them, as with two checkouts without context today. A checkout without `context` creates no attempt and its retries are not idempotent.

## Access, seats, and tokens

An active, trialing, or past-due personal subscription grants access through its unexpired period. A seat grants access while its package is active. Non-revoked operator-granted free access grants access without an end date. When no access remains the state is `read_only` (`ends_at` is the latest ended subscription/package/seat/free-access event, or null if the person never had access). Operator decision 2026-10-16: nothing is deleted automatically; there is no deletion date, no deletion notice and no state `none`; the app keeps export and download available. A later deletion policy is a separate decision.

Team owners manage seats through `GET|POST|DELETE <prefix>/team/seats/`; each package enforces its purchased seat count and only its owner may modify seats. Operator commands `billing_grant_free_access <user-id|email> --reason ...` and `billing_revoke_free_access` grant and revoke free access. `billing_expire_tokens` closes expired grants.

Token balances use an append-only ledger. Term grants expire at term end; a period-specific reference prevents duplicate grants across checkout, subscription events, invoice events, and reconciliation. Purchases have no expiry while the owner has access. Debits lock the account, consume earliest-expiring grants before purchases, reject insufficient balances, and are idempotent by reference. Expired grants are not spendable even before the expiry command runs. `tokens_for(rate_name, units)` multiplies integer units by the declared rate.

## EU/EEA consumer withdrawal

`DCM_BILLING_WITHDRAWAL_ENABLED` defaults to `True`; setting it to false makes all purchases ineligible with `withdrawal_disabled`. A purchase is eligible while active, at most 14 days old (inclusive), billed to a country in the EU/EEA list below, and without a business tax ID in Checkout. Switzerland and business purchases are excluded. The list is Austria, Belgium, Bulgaria, Croatia, Cyprus, Czechia, Denmark, Estonia, Finland, France, Germany, Greece, Hungary, Ireland, Italy, Latvia, Lithuania, Luxembourg, Malta, Netherlands, Poland, Portugal, Romania, Slovakia, Slovenia, Spain, Sweden, Iceland, Liechtenstein, and Norway.

`GET <prefix>/withdrawal/` lists the signed-in user's purchases and eligibility. `GET <prefix>/withdrawal/<id>/preview/` returns `eligible`, `reason`, `amount_paid_cents`, `currency`, `time_share_cents`, `token_share_cents`, `used_cents`, `refund_cents`, and a display-ready `calculation` line. `POST <prefix>/withdrawal/<id>/` with `{"confirm":true}` records one withdrawal. Other users' purchase IDs return 404; an ineligible purchase returns 409 with its reason code. Repeated confirmation returns the stored result with `already_withdrawn:true`.

Money is calculated in integer cents. For a time-based purchase, used time is whole days from term start rounded up and capped at term length; the time share is rounded up to the next cent. Token usage is consumed tokens from the purchase's grant references divided by tokens granted, times the paid amount, also rounded up. The used amount is the larger share and the refund is paid amount less used amount, floored at zero. Token packages use only the token share. Example: on a 1,000-cent 10-day term, 2 days used costs 200 cents; if 30% of its tokens were consumed, the token share is 300 cents, so the refund is 700 cents. Expired remaining tokens do not count as consumed.

Execution locks the purchase and ends the associated subscription or team package, records token expiry ledger entries, then requests the proportional refund from Stripe with a stable idempotency key. A Stripe failure leaves the local withdrawal unmarked so the confirmation can be retried. Subscription cancellation may already have reached Stripe; retries accept an already-canceled or missing subscription. A withdrawal email includes the date, plan, amount, calculation and original-payment-method timing; an email transport error is logged by exception class and does not reverse the withdrawal. Subscription events cannot reactivate a withdrawn purchase.

**Completing a withdrawal from Stripe's refund event (DCM-BILL-4).** The refund is created with the metadata `dcm_withdrawal` = `dcm-withdrawal:<app>:<purchase id>` (also its idempotency key), `app` and `purchase_id`. Ending the access, expiring the purchase's remaining tokens, marking the purchase `withdrawn` and registering the confirmation e-mail is ONE routine (`withdrawal.complete_withdrawal`) that locks the purchase row, re-reads the status under the lock and does nothing unless the purchase is still `active`. The POST path calls it right after `Refund.create`; the webhook and `billing_reconcile` call it for a refund event. So if the server crashes after Stripe accepted the refund but before the local commit, the refund event completes the withdrawal without any action by the person; whichever path runs second finds the purchase withdrawn (the POST then answers `already_withdrawn: true` with the stored result, one e-mail, one set of ledger rows). A refund counts as a withdrawal only if its `metadata.dcm_withdrawal` (or, for the refund events of refunds created by 3.5.0 to 3.6.0 before the metadata key existed, the idempotency key of the request that created it, carried by the event) is the key of an ACTIVE purchase of this app, its status is `succeeded`, its payment intent equals the purchase's, its currency equals the purchase's and its amount is an integer between 1 and the amount paid; manual refunds in the Stripe dashboard and disputes never match and keep being recorded as no-ops. Handled events: `refund.created`, `refund.updated` (the event object is the refund) and `charge.refunded` (only when an active purchase has the charge's payment intent: the charge's refunds are then listed from the API). The Stripe webhook endpoint of each app must subscribe at least `refund.created` and `refund.updated`. `billing_reconcile` additionally lists the refunds since `--since` and completes pending withdrawals, so a missed webhook is repaired. Such an older refund is therefore completed from its `refund.created`/`refund.updated` event (also through `billing_reconcile`'s event pass), but not from `charge.refunded` or the refund list, which carry no request key.

Apps can reverse an action debit after a provider fault with `reverse_debit(owner, reference=action_id, reason=...)`. The service writes one non-expiring adjustment entry with reference `reversal:<action_id>` for exactly the debit amount; repeated calls return that entry. Unknown debits raise `ValueError("unknown_debit")` and an empty reason is rejected.

## Operator data and the account view (DCM-BILL-5)

**Operator endpoints** (all under the billing url prefix). Operator means exactly the accounts allowed to use the shared Users tab (`can_view_users_admin`), no new role. The guard runs first for every method: anonymous -> 401 `{"detail": "authentication_required"}`, any other account -> 403 `{"detail": "forbidden"}`, an unsupported method by an operator -> 405. Every response is `no-store`, aggregates and account rows never contain Stripe ids, ledger references, payloads or secrets.

- `GET operator/overview/?month=YYYY-MM` (default the current UTC month, invalid -> 400 `invalid_month`) returns aggregates only: `accounts.active` and `accounts.by_source` (`subscription` per plan key, `team_seat`, `free_access`; each person counted once, as of now, by the precedence of `access_state`), `subscriptions.new` (first payments in the month) and `.ended`, `team` (`packages_active`, `packages_sold`, `seats_total`, `seats_used`), `token_packages` (`sold`, `tokens`), `tokens` (`granted`, `used`, `expired`; withdrawals are not expiry; ledger rows tagged with another app are ignored), and `ai_cost` (`calls`, `usd_micros`, `by_model` sorted by cost, `usd_micros_per_used_token` or null) from this app's AI cost records. The number of queries does not depend on the data.
- `GET operator/accounts/?q=&page=&page_size=` lists people (email, name, `access` as `access_state` returns it, `plan_key`, `term_ends_at`, `token_balance`, `free_access` with reason, `granted_by`, `granted_at`), ordered by email. `q` (at most 100 characters, else 400 `invalid_query`) searches email, names and username case-insensitively; `page` 1 to 10000 and `page_size` 1 to 100 (default 25), else 400 `invalid_page`. The values equal what `access_state` and `token_balance` return, computed for the page with a constant number of queries.
- `POST operator/accounts/<user_id>/free-access/` `{"reason": "..."}` grants free access (reason mandatory, at most 1000 characters, else 400 `reason_required`; 201, or 200 with the existing grant when one is already open) and `DELETE` revokes every open grant (200, `revoked` true or false). Unknown person 404 `user_not_found`, CSRF enforced. Both answer `{user_id, free_access, access}`. The operator's email is stored as `granted_by`, the audit log entry carries the operator as actor, concurrent switches for one person are serialised, and revoking deletes nothing: subscriptions, tokens and seats stay and the person falls under the read-only rules.

**`GET <prefix>/me/`** keeps its keys and adds `subscription` (null, or `plan_key`, `plan_name`, `status`, `current_period_start`, `current_period_end`, `renews`, `cancel_at_period_end`), `tokens.grants` (the person's own credits that still count, `source` `plan` or `package`, `amount`, `remaining`, `expires_at` or null, soonest expiry first, no ids or references), `team` (null unless the person pays for an active team package: `plan_key`, `plan_name`, `ends_at`, `seats_total`, `seats_used`, `seat_holders` with `user_id`, `name`, `email`) and, for a person who holds a seat, `seat` (`plan_key`, `plan_name`, `ends_at`) without any other holder. The response is `no-store`. Token origins, team pool and seat payer (DCM-BILL-6, additive): every `tokens.grants` row also carries `origin` (`plan` for a subscription or team allowance with purpose `plan:<key>`, `package` for a purchased package, `trial` for purpose `trial`, `credit` for every other grant; `source` is unchanged). `tokens.pool` is always present: `null`, or `{plan_key, plan_name, amount, remaining, expires_at}` for the payer of an active team package and for a seat holder of one (the payer's own package wins when a person is both): the sum of the package account's unexpired grant and purchase entries, `remaining` may be 0, `expires_at` is the package end; it is never part of `tokens.grants` or `tokens.balance` and is `null` once the package ended. A seat holder's `seat` also carries `payer_name`: the owner's full name, else the e-mail, else the username, never an id.

## API responses and security

`GET <prefix>/me/` requires a session-authenticated user and returns `access` (`status` is `active` or `read_only`, `source`, `ends_at`), `tokens.balance`, and declared `plans`. `GET <prefix>/cost-report/?month=YYYY-MM` defaults to the current UTC month and requires `Authorization: Bearer <token>`; missing or incorrect tokens return an empty 401. It returns only app/month, aggregate call and token counts, aggregate USD micros, token debits, and grouped provider/model totals. No user identifiers or email addresses are returned.

The API never returns Stripe secret keys, webhook secrets, signatures, or raw event payloads. Stripe SDK and YAML imports are lazy; importing the billing package does not require the optional `django-core-micha[billing]` extra. No Django admin registration is provided.

## Operator commands

- `billing_check`: validate the declared YAML file.
- `billing_sync_plans [--dry-run]`: create versioned Stripe Products and Prices.
- `billing_reconcile [--since ISO_TIMESTAMP]`: reconcile subscriptions and events.
- `billing_grant_free_access <user-id|email> --reason ...` / `billing_revoke_free_access`: manage operator access.
- `billing_expire_tokens`: close expired token grants.
