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

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and invariants so that UCM-BILL-2 (UI) builds against them. One dispatch; complete, no stubs. These endpoints expose the billing data of other accounts: when in doubt, refuse (403) and return less.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (docs, version and register are the Orchestrator's), `billing/withdrawal.py`, `billing/stripe_service.py`, models and migrations (no schema change is needed: read the models as they are). Run tests with the interpreter named in your dispatch note.

### Context package (3.6.1, paths under `src/django_core_micha/billing/`)

- `views.py`: plain Django JSON views (session auth, `@require_GET` / `@require_http_methods`, `@csrf_protect` on mutations), `me(request)` (returns `{"access": {status, source, ends_at}, "tokens": {"balance"}, "plans"}`), `team_seats`, `_plans()` (plan rows from `billing.yaml` with `key`, `name`, `kind`, ...), `cost_report` (the month parsing and the `AICostRecord` aggregation over `app == DCM_BILLING_APP` are the pattern for the overview). `urls.py` lists the routes by name.
- `models.py` (read-only for you): `Subscription` (`user`, `plan_key`, `status`, `current_period_start/end`, `cancel_at_period_end`, `ended_at`), `TeamPackage` (`owner`, `plan_key`, `seats`, `starts_at`, `ends_at`, `stripe_ref`), `TeamSeat` (`package`, `user`, `active`, `created_at`, `removed_at`), `FreeAccess` (`user`, `granted_by_label`, `reason`, `granted_at`, `revoked_at`), `TokenAccount` (per `user` or per `team_package`), `TokenEntry` (`account`, `kind` grant|purchase|debit|expiry|adjustment, signed `amount`, `remaining`, `expires_at`, `purpose`, `reference`, `app`, `created_at`), `Purchase` (`kind`, `plan_key`, `amount_paid_cents`, `purchased_at`, `tokens_granted`, `status`, ...), `AICostRecord` (`provider`, `model`, `user`, `input_tokens`, `output_tokens`, `cache_tokens`, `cost_usd_micros`, `app`, `created_at`).
- `services.py`: `access_state(user)` (precedence: free access, then subscription/team by latest end; sources `"subscription"`, `"team"`, `"free"`), `token_balance(owner)` (unexpired credits with `remaining > 0`), `models_q_expiry(now)`.
- Operator check: `django_core_micha.auth.permissions.can_view_users_admin(user, request=request)` is the permission the shared Users tab (`BaseUserViewSet`) uses. It is THE operator check for every operator endpoint here, for reading and for the free-access switch; no new role, no new setting.
- Existing commands to mirror: `management/commands/billing_grant_free_access.py` (creates a `FreeAccess`, `granted_by_label="operator"`, reason required) and `billing_revoke_free_access.py` (sets `revoked_at` on the latest non-revoked row).
- Audit: `FreeAccess` is registered in `billing/audit_config.py`, so create/update are audited through the audit log; the actor comes from `django_core_micha.auditlog.audit_context` (`set_current_actor(user)` / `reset_current_actor(token)`), which the audit middleware sets for requests in a host app. The new views set the actor explicitly around the write (and reset it in `finally`) so the audit entry carries the operator also where that middleware is absent, as in the tests.
- Tests to model on: `tests/test_*.py` in `billing/tests/` (fixtures create users with `get_user_model().objects.create_user`; `conftest.py` makes factory requests skip CSRF), the existing `me` and cost-report tests.

### Fixed design

New module `billing/operator.py` (views) plus the extension of `me` in `views.py`; routes in `urls.py` named `billing-operator-overview`, `billing-operator-accounts`, `billing-operator-free-access`.

**Guard (all operator endpoints).** Anonymous -> 401 `{"detail": "authentication_required"}`; authenticated but `can_view_users_admin` false -> 403 `{"detail": "forbidden"}` and NOTHING of the data is touched or counted before the check. Every response `Cache-Control: no-store`. Never include a `stripe_*` id, a ledger `reference`, a payload, a card detail or a secret in any response of this order.

1. `GET operator/overview/?month=YYYY-MM` (default: the current UTC month; invalid -> 400 `{"detail": "invalid_month"}`, same parsing as the cost report). Response, all integers unless noted, aggregates only, scoped to `DCM_BILLING_APP`:
   `{"app", "month", "accounts": {"active": n, "by_source": {"subscription": {"<plan_key>": n, ...}, "team_seat": n, "free_access": n}}, "subscriptions": {"new": n, "ended": n}, "team": {"packages_active": n, "packages_sold": n, "seats_total": n, "seats_used": n}, "token_packages": {"sold": n, "tokens": n}, "tokens": {"granted": n, "used": n, "expired": n}, "ai_cost": {"calls": n, "usd_micros": n, "by_model": [{"provider", "model", "calls", "usd_micros", "input_tokens", "output_tokens", "cache_tokens"}], "usd_micros_per_used_token": n or null}}`.
   Definitions, fixed: `accounts.active` and `by_source` count each person ONCE, as of now, by the precedence of `access_state` (free access first, then the later-ending of subscription and team seat), using set queries, not one `access_state` call per user; `subscriptions.new` = `Purchase` rows of kind `subscription` with `purchased_at` in the month, `subscriptions.ended` = `Subscription.ended_at` in the month; `team.packages_active` = packages active now, `packages_sold` = `Purchase` kind `team_package` in the month, `seats_total` = sum of `seats` of the active packages, `seats_used` = active seats in them; `token_packages` = `Purchase` kind `token_package` in the month (`tokens` = sum of `tokens_granted`); `tokens.granted` = sum of `amount` of `grant`/`purchase` entries created in the month, `used` = minus the sum of `debit` amounts in the month, `expired` = minus the sum of `expiry` amounts in the month whose `reference` does not start with `withdrawal:` (withdrawals are not expiry); `ai_cost` from `AICostRecord` with `app == DCM_BILLING_APP` and `created_at` in the month, `by_model` sorted by cost descending, `usd_micros_per_used_token` = integer division rounded half up of cost by `tokens.used`, `null` when no tokens were used. Month boundaries are UTC. The number of queries is constant (no per-plan or per-user loops against the database; group in Python or with `values().annotate()`).
2. `GET operator/accounts/?q=&page=&page_size=` (`page` from 1, default 1; `page_size` default 25, maximum 100, invalid values -> 400 `{"detail": "invalid_page"}`; `q` at most 100 characters (longer -> 400 `{"detail": "invalid_query"}`), case-insensitive substring over `email`, `first_name`, `last_name`, `username`; empty `q` lists everyone, ordered by `email` then `pk`). Response `{"count", "page", "page_size", "results": [{"user_id", "email", "name", "access": {"status", "source", "ends_at"}, "plan_key", "term_ends_at", "token_balance", "free_access": null or {"reason", "granted_by", "granted_at"}}]}` where `name` is the full name or `""`, `source` and `status` are exactly those `access_state` returns (`"free"`, `"subscription"`, `"team"`, or null), `plan_key` is the plan of the subscription or team package that grants the access (null for free access or none), `term_ends_at` the end of that subscription period or package (null otherwise), `token_balance` as `token_balance(user)` counts it and `free_access` the latest non-revoked row. The numbers MUST equal what `access_state(user)` and `token_balance(user)` return for that user (a test compares them for every user on a page), but be computed for the whole page with a constant number of queries: fetch the page of users first, then the subscriptions, seats (with packages), free-access rows and token entries for exactly those user ids in bulk (`filter(user__in=ids)` / `account__user__in=ids`, aggregate per user in Python); reuse the rules of `access_state` rather than inventing new ones, and do not call `access_state` per row. A test fixes the query count: the same count for a page of 3 and a page of 25 users.
3. `POST operator/accounts/<int:user_id>/free-access/` body `{"reason": "<text>"}` and `DELETE` on the same path. `@csrf_protect`, operator guard first. Unknown user -> 404 `{"detail": "user_not_found"}`. POST: `reason` must be a non-empty string after strip, at most 1000 characters, else 400 `{"detail": "reason_required"}`; if a non-revoked `FreeAccess` already exists answer 200 with it and change nothing, else create it with `granted_by_label` = the operator's email (falling back to the username), truncated to 200 characters, reason as given, answer 201. DELETE: set `revoked_at=now` on every non-revoked row of that user in one update, answer 200 (also when there was none: `{"revoked": false}`); NOTHING else is deleted or changed (no subscription, token or seat row; the person keeps the data and falls under the read-only rules of `access_state`). Both answer the account state `{"user_id", "free_access": null or {"reason", "granted_by", "granted_at"}, "access": {"status", "source", "ends_at"}}`. Writes run inside `transaction.atomic()` with the audit actor set to the operator around them (see context package) so the audit entry shows who and when; the audit log is not extended, no new model.
4. **`me/` richer** (keep every existing key and its meaning; only ADD keys; anonymous stays 401): `subscription` = `null` or `{"plan_key", "plan_name", "status", "current_period_start", "current_period_end", "renews": bool, "cancel_at_period_end": bool}` for the person's current subscription (the one `access_state` would count; otherwise the most recent not-ended one; null if none), `renews` is true only for status active/trialing with `cancel_at_period_end` false and the period end in the future; `plan_name` from `_plans()` or `""` when the plan is no longer in `billing.yaml`. `tokens.grants` = list of the person's OWN personal-account `grant`/`purchase` entries that still count (remaining > 0 and not expired, i.e. what `token_balance` sums, plus `adjustment` credits is NOT included): `{"source": "plan" for kind grant, "package" for kind purchase, "amount", "remaining", "expires_at" (ISO or null)}`, ordered by expiry (soonest first, no-expiry last); no ledger reference, no id. `team` = `null` unless the person OWNS a package that is active now (the same package `team_seats` uses): `{"plan_key", "plan_name", "ends_at", "seats_total", "seats_used", "seat_holders": [{"user_id", "name", "email"}]}` for the active seats of that package only; a person who merely HOLDS a seat gets `seat` = `{"plan_key", "plan_name", "ends_at"}` (the package they hold a seat in) and never sees the other holders or the owner's email; the two keys are never both leaked to the wrong role: owner gets `team`, holder gets `seat`, someone with both gets both for their own packages only.
5. **No schema change, no new setting, no change of existing endpoints beyond the added `me/` keys.** Existing `me/` tests must keep passing unchanged.

### Tests (new files only: `billing/tests/test_operator_endpoints.py`, `billing/tests/test_me_details.py`)

Operator guard: anonymous 401, normal account 403 on ALL three operator endpoints and on both methods of the switch, operator 200; to build an operator in the tests use a superuser (`is_superuser=True`) and, for a normal account, a plain user with `override_settings` leaving `INVITE_MIN_ROLE_LEVEL`/roles at their defaults. Overview: a seeded month with one person per access source (subscription on two plans, team seat, free access), a new and an ended subscription, an active and a sold team package with used seats, a token package, grants/debits/expiry (a `withdrawal:` expiry must not count) and AI cost records of two models plus a record of another app and another month that must not count; assert every aggregate, the by-model order and the cost per used token (and null without usage), the invalid month 400, and a constant query count (`django_assert_max_num_queries` or `CaptureQueriesContext`) for a bigger seed. Account list: search on email, name and username fragments, case-insensitive, `q` over 100 characters -> 400 `invalid_query`, bad `page`/`page_size` -> 400 `invalid_page`, pagination (`count`, `page`, `page_size`, last page, page beyond the end gives an empty `results`), the values equal `access_state`/`token_balance` for every row (parametrise over: none, subscription, team seat, free, expired subscription, revoked free access), query count identical for 3 and 25 rows. Free access: grant needs a reason (missing, blank, over 1000 -> 400), grant creates one row and a second grant changes nothing, the audit log has an entry whose actor is the operator (look at how the auditlog tests read `AuditEvent`), the label is the operator's email, revoke sets `revoked_at` on all open rows, keeps subscriptions/tokens/seats untouched and the person becomes read-only, revoke without a grant answers `{"revoked": false}`, unknown user 404, CSRF is enforced on both (a test with `enforce_csrf_checks`). `me/`: subscription block (renews true/false, cancel at period end, plan name, null without one, unknown plan -> `plan_name` empty), `tokens.grants` with expiry soonest first and no reference/id keys, exhausted and expired grants absent, team payer sees seat holders with name and email, a seat holder sees `seat` but not `team` and no other holder, a person with neither gets `team: null` and no `seat`. Secrets: serialise every response body of this order and assert no `stripe`, `sk_`, `whsec`, `payload`, or ledger reference string appears. Existing billing tests stay green.

### Invariants

Every operator endpoint checks the same permission as the Users tab first; aggregates only in the overview; emails only to operators and to the team payer for their own seat holders; the account list is bounded and constant-query; free-access changes never delete data; no secret, reference or raw payload in a response; integer arithmetic for money and tokens.

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

Gate #1 approved by the operator 2026-10-17 (in the Maana Expertenchat, session local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Seams: `billing/views.py` (`me`, `team_seats`), `billing/services.py`
(`access_state`, `token_balance`, grants), `billing/models.py` (FreeAccess, TeamPackage, TeamSeat,
token ledger, AI call records), the free-access management commands, the permission used by the
shared Users tab. Tier 3: all lenses plus `sec_reviewer`. Release per the dcm publish flow.
