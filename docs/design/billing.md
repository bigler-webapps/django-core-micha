# Shared billing contract

`django_core_micha.billing` is opt-in. An application installs the app and mounts `billing.urls` at its chosen API prefix; applications that do not install it receive no billing tables or routes. bigler-consult is the seller and uses one Stripe account with Managed Payments. Stripe Checkout and the Stripe customer portal are the only payment flows in this contract.

## Configuration

Settings are read with `getattr(settings, name, default)`. Configure `DCM_BILLING_APP` to the application key, `DCM_BILLING_FILE` to its UTF-8 `billing.yaml` (default: `BASE_DIR / "billing.yaml"`), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `DCM_BILLING_SUCCESS_URL`, `DCM_BILLING_CANCEL_URL`, and `DCM_BILLING_PORTAL_RETURN_URL`. The cost report accepts a bearer token whose lowercase SHA-256 digest is in `DCM_BILLING_REPORT_TOKEN_SHA256`. Secret values must not be logged or returned.

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

Every event is checked against `metadata.app`; invoice processing also looks at invoice line metadata or retrieves its subscription when necessary. `billing_reconcile [--since ISO_TIMESTAMP]` (default seven days ago) lists subscriptions and recent events, filters by app metadata client-side, and applies the same idempotent handlers. Reconciliation failures report an exception class, not provider data.

## Access, seats, and tokens

An active, trialing, or past-due personal subscription grants access through its unexpired period. A seat grants access while its package is active. Non-revoked operator-granted free access grants access without an end date. When no access remains the state is `read_only` (`ends_at` is the latest ended subscription/package/seat/free-access event, or null if the person never had access). Operator decision 2026-10-16: nothing is deleted automatically; there is no deletion date, no deletion notice and no state `none`; the app keeps export and download available. A later deletion policy is a separate decision.

Team owners manage seats through `GET|POST|DELETE <prefix>/team/seats/`; each package enforces its purchased seat count and only its owner may modify seats. Operator commands `billing_grant_free_access <user-id|email> --reason ...` and `billing_revoke_free_access` grant and revoke free access. `billing_expire_tokens` closes expired grants.

Token balances use an append-only ledger. Term grants expire at term end; a period-specific reference prevents duplicate grants across checkout, subscription events, invoice events, and reconciliation. Purchases have no expiry while the owner has access. Debits lock the account, consume earliest-expiring grants before purchases, reject insufficient balances, and are idempotent by reference. Expired grants are not spendable even before the expiry command runs. `tokens_for(rate_name, units)` multiplies integer units by the declared rate.

## API responses and security

`GET <prefix>/me/` requires a session-authenticated user and returns `access` (`status` is `active` or `read_only`, `source`, `ends_at`), `tokens.balance`, and declared `plans`. `GET <prefix>/cost-report/?month=YYYY-MM` defaults to the current UTC month and requires `Authorization: Bearer <token>`; missing or incorrect tokens return an empty 401. It returns only app/month, aggregate call and token counts, aggregate USD micros, token debits, and grouped provider/model totals. No user identifiers or email addresses are returned.

The API never returns Stripe secret keys, webhook secrets, signatures, or raw event payloads. Stripe SDK and YAML imports are lazy; importing the billing package does not require the optional `django-core-micha[billing]` extra. No Django admin registration is provided.

## Operator commands

- `billing_check`: validate the declared YAML file.
- `billing_sync_plans [--dry-run]`: create versioned Stripe Products and Prices.
- `billing_reconcile [--since ISO_TIMESTAMP]`: reconcile subscriptions and events.
- `billing_grant_free_access <user-id|email> --reason ...` / `billing_revoke_free_access`: manage operator access.
- `billing_expire_tokens`: close expired token grants.
