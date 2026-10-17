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

## Part B — Implementation map (implementer)

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and the rules that decide the edge cases. The shapes below were already sent to the UI session (UCM-BILL-6) and are binding. One dispatch; complete, no stubs. `me/` shows data of other accounts to team payers and seat holders: when in doubt, show less.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (docs, version and register are the Orchestrator's), models and migrations, `billing/operator.py`, `billing/withdrawal.py`, `billing/stripe_service.py`, and the files `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` (another session's uncommitted work: leave them exactly as they are). Run tests with the interpreter named in your dispatch note.

### Context package (3.7.0 to 3.12.0, paths under `src/django_core_micha/billing/`)

- `views.py::me(request)` builds the response (read it fully first, about 30 lines): `access` from `access_state`, `subscription` (current or most recent not-ended one), `grants` = the person's OWN personal-account entries of kind `grant`/`purchase` with `remaining > 0` that have not expired, ordered by expiry soonest first, each `{"source": "plan" if kind == "grant" else "package", "amount", "remaining", "expires_at"}`, `owned` = the active `TeamPackage` the person owns (`owner=request.user`, `starts_at <= now < ends_at`, earliest end first), `held` = the active `TeamSeat` (`active=True`, package active) of the person, `team` for the owner (plan key/name, `ends_at`, `seats_total`, `seats_used`, `seat_holders` with name and email), and `seat` only for a holder (`plan_key`, `plan_name`, `ends_at`). The result is `{"access", "tokens": {"balance", "grants"}, "plans", "subscription", "team"}` plus `seat` when held, sent `Cache-Control: no-store`. `plans` is a dict key -> name from `_plans()` inside the view.
- `models.py` (read-only for you): `TokenAccount` (`user` XOR `team_package`; a team package's pool is the account with `team_package=<package>`), `TokenEntry` (`account`, `kind` grant|purchase|debit|expiry|adjustment, `amount`, `remaining`, `expires_at`, `purpose`, `reference`), `TeamPackage` (`owner`, `plan_key`, `seats`, `starts_at`, `ends_at`), `TeamSeat` (`package`, `user`, `active`). Grant purposes in the wild: a subscription period allowance is `plan:<plan key>` (`stripe_service._grant_period`), a team package allowance is `plan:<plan key>` with reference `stripe:team:<ref>` (team package checkout), a purchased token package is a `purchase` entry (reference `stripe:checkout:<session>`), the sign-up trial is a grant with purpose `trial` created by the app (`grant_tokens(user, n, purpose="trial", ...)`), and operator/other credits are grants with any other purpose.
- `services.py`: `token_balance(owner)` (unexpired credits with remaining > 0), `models_q_expiry(now)`, `grant_tokens`.
- Tests to model on: `tests/test_me_details.py` (helper `get(user, monkeypatch)` patches `views._plans`; existing team payer / holder / non-member cases and the grants test), `tests/test_operator_endpoints.py` for the style.

### Fixed design

All three additions are additive; no existing key changes meaning, order or presence.

1. **`tokens.grants[].origin`.** Every grant row gets `origin`, computed from the entry: ledger `kind == "purchase"` -> `"package"`; `kind == "grant"` and `purpose` starts with `"plan:"` -> `"plan"`; `kind == "grant"` and `purpose == "trial"` -> `"trial"`; every other `kind == "grant"` -> `"credit"`. The existing `source` stays exactly as it is today ("plan" for any grant, "package" for a purchase), so a trial or credit still has `source: "plan"` and a different `origin`. One small pure helper for the mapping (module level in `views.py` or a helper module), so it can be tested on its own. Purpose is never returned.
2. **`tokens.pool`** (key ALWAYS present inside `tokens`; `null` when there is none). The package: `owned` if the person owns an active team package, else `held`'s package (a person who both owns one and holds a seat elsewhere gets the owned one; documented). The pool is the `TokenAccount` with `team_package=<that package>`; its pool entries are the entries of kind `grant`/`purchase` whose `expires_at` is null or in the future (NOT filtered by `remaining`, so a used-up pool shows `remaining: 0`). If the package has no such account or no such entry, `pool` is `null`. Otherwise `pool = {"plan_key": package.plan_key, "plan_name": plans.get(package.plan_key, ""), "amount": sum of entry.amount, "remaining": sum of max(0, entry.remaining or 0), "expires_at": package.ends_at.isoformat()}`. Integers for amounts. After the package has ended (not active) `owned` and `held` are empty, so the pool is `null`. The pool is NEVER added to `tokens.grants` and `tokens.balance` stays the personal balance. Do not expose entry ids, references or purposes. A person who is neither payer nor seat holder gets `null`.
3. **`seat.payer_name`.** The `seat` block (still only for a seat holder, never for the payer) gains `payer_name`: the package owner's `get_full_name()` stripped, else the owner's `email`, else the owner's `username` (a string, never empty). It reads the owner through `select_related`, no extra query per field. The owner's e-mail otherwise stays hidden from the holder (`seat` has no other owner field).
4. **Queries.** Keep the number of queries constant (no per-entry or per-holder loop): one query for the pool entries (aggregate with `Sum` or fetch the few rows once), the owner via the held seat's `select_related("package__owner")`.
5. **Unchanged:** every other `me/` key (`access`, `subscription`, `team`, `plans`, `seat.plan_key/plan_name/ends_at`), the no-store header, the 401 for anonymous users, the operator and withdrawal endpoints, how tokens are spent. No schema change, no new setting.

### Tests (extend `tests/test_me_details.py`; existing tests stay green unchanged)

`origin` for each kind: a subscription allowance (`grant`, purpose `plan:basic`) -> plan, a team allowance as seen by the payer's own personal account is not applicable: test the mapping helper directly for `plan:team` too, a purchased package (`purchase`) -> package, a `trial` grant -> trial, an operator credit (a grant with purpose `operator credit` or empty) -> credit; existing `source` unchanged for all of them and `purpose` never in the output. `tokens.pool`: the payer of an active team package with a team allowance (account with `team_package`) sees plan key/name, amount, remaining (partly used, then fully used showing 0), `expires_at` equal to the package end; a seat holder sees the same pool of the package they hold a seat in; a person without a team gets `pool: null` and the key is present; after the package ended (`ends_at` in the past) the pool is `null` for payer and holder; a package without a token account or only with an expired grant gives `null`; the pool is not part of `tokens.grants` and not part of `tokens.balance`; a person who owns one package and holds a seat in another gets the owned pool. `seat.payer_name`: full name when set (first and last name), the e-mail when no name is set, the username only when the e-mail is also empty; the payer never has `seat`; the holder sees no other owner field and no other holder. Regression: the complete key set of the existing `me/` response is unchanged apart from the additions (assert the old keys and their old values for a subscription person, a team payer and a seat holder). A constant query count for a payer with many seat holders and grants (`django_assert_max_num_queries` with a number you measure on the existing test, then the same number with 10 more seat holders). No secret, reference or id of a ledger entry in any response (serialise and search).

### Invariants

Additive only; the pool is shown only to the payer and seat holders of an ACTIVE package, never as part of the personal balance or grants; no references, purposes or entry ids leak; the payer's e-mail appears to a holder only as `payer_name` when no name exists; a constant number of queries.

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

Gate #1 approved by the operator 2026-10-17 („UCM-BILL-6 machen wir noch", in the Maana Expertenchat, session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce); this order supplies the data UCM-BILL-6 needs. Seams:
`billing/views.py::me` (grants list, team, seat), `TeamPackage` pool account, `TokenEntry.purpose`. Tier 3: all
lenses plus `sec_reviewer` (who may see the pool and the payer's name).
