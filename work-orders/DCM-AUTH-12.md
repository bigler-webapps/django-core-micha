# DCM-AUTH-12 — Backend foundations for resumable setup: terms acceptance and checkout return

## Part A — Envelope (everyone)

**Repository:** django-core-micha, `main`. **Tier:** 3 (shared core, authenticated writes, schema and billing).

### Goal and expected outcome

Provide the two missing shared backend capabilities for Maana's first-use wizard:
an explicit, attributable terms-of-use acceptance and an unambiguous return from
Stripe Checkout to the originating flow. Existing consumers retain their current
behaviour unless they opt into these capabilities.

The operator requested these three implementation work orders on 2026-10-21,
following the approved MAANA-APP-18 prototype. They explicitly chose server-side
setup continuation, preparation before final processing confirmation, a 24-hour
lifetime for prepared imports, and explicit acceptance of the terms of use.

### Delivery order and dependencies

1. **This WO first:** publish the backend contract and an installable DCM release.
2. [UCM-AUTH-14](../../ui-core-micha/work-orders/UCM-AUTH-14.md) consumes that contract
   and publishes the shared presentation/client release.
3. [MAANA-APP-19](../../maana/work-orders/MAANA-APP-19.md) adopts both exact releases,
   enables the capabilities and implements the persistent app journey.

This WO has no dependency on either downstream implementation. Its public contract
is the authority for downstream payloads and errors. No circular dependency on
Maana models, routes, project IDs or wizard step names is allowed. Release numbers
are assigned at release time; downstream WOs use the actual published versions.

### Scope

**1. Explicit terms acceptance, opt-in per app.**

- Add a shared persistence/service boundary and authenticated read/accept API for
  the current user's acceptance of an app's configured terms document.
- Record the app identity, authenticated user, server-configured document version
  and URL, and server acceptance timestamp. Use a stable, app-owned version
  identifier; it is not supplied authoritatively by the browser. A submitted
  version must match the version actually offered by the server.
- Acceptance requires an explicit affirmative submission. Missing/false acceptance,
  stale versions and unknown/unconfigured documents fail without writing a record.
  Returning the current document metadata makes stale-version recovery possible.
- Repeated acceptance of the same user/app/version is idempotent, including
  concurrent requests; preserve the original evidence timestamp. A later version
  does not overwrite the historical record. No IP address or user-agent collection
  is required for this feature.
- Existing privacy acceptance and optional convenience-cookie preferences remain
  separate. Never infer terms acceptance from either field, an existing account,
  a Google/Microsoft login, a successful checkout or an administrator's edit.
- Normal callers can read/write only their own acceptance. Enforce session/CSRF
  protections; do not widen generic user-profile editing or user administration.
- Provide a server-side check for consuming apps to gate their setup operations.
  Enabling this capability does not introduce a global login/access gate for all
  users or all apps. Maana decides where its new setup journey requires it.
- Include the necessary DCM-owned schema migration and consumer installation
  instructions. Avoid forcing abstract-profile field migrations across the fleet.

**2. Explicit checkout return context.**

- Extend checkout with an optional app-configured context key. The server resolves
  success/cancellation destinations through an allowlisted configuration or hook;
  arbitrary browser-provided return URLs are not accepted. Omitted context retains
  the existing configured destinations and response compatibility.
- Bind each contextual checkout attempt to its authenticated user, app, plan and
  context, and expose an opaque correlation reference for resumption. Bind the
  Stripe session to this attempt. The return must distinguish a setup checkout
  from a later account checkout, including multiple tabs and repeated visits.
- Provide authenticated, ownership-checked resolution of that reference. Returning
  from Stripe or adding a success query parameter is never proof of payment or
  entitlement. Existing verified billing/webhook state remains authoritative.
- Support success with settlement still pending, cancellation, failure and retry.
  Retrying the same attempt after a network interruption must not create a second
  Stripe session or duplicate grants; a genuinely new attempt is explicit.
- Preserve existing subscription/token-package rules, webhook verification and
  grant idempotency. Include only the persistence necessary for this correlation,
  not a generic workflow engine.

**3. Public contract and release.**

Document settings/hooks, request/response/error shapes, idempotency, ownership,
required migrations and omitted-option compatibility in the repo's API contract
and consumer documentation. Publish a minor release for the new public APIs and
document adoption. No consumer pin changes belong to this WO.

### Non-goals / do not touch

- Maana's draft state, uploads, projects, processing, prices and trial policy.
- A general onboarding progress database, legal-text editor, legal content changes,
  forced fleet-wide reacceptance, consent withdrawal administration or operator UI.
- Auth provider configuration, signup/login semantics, MFA, account linking and
  automatic login after email confirmation.
- Infrastructure, CI changes, secrets, unrelated dependency upgrades and consumer
  releases. Existing foreign sync-secrets edits are outside this WO.

### Acceptance and required tests to write

- Valid self-acceptance persists the correct app/version/URL and server timestamp;
  concurrent/repeated submissions yield one logical record and stable evidence.
- Anonymous, foreign-user/app, missing/false, stale-version and forged-timestamp
  requests cannot create or alter acceptance. A profile PATCH cannot forge it.
- Privacy-only and social-login-only users remain unaccepted; a new document
  version requires an explicit new acceptance without rewriting history.
- Unconfigured consumers retain existing auth/profile and checkout behaviour.
- Checkout context maps only to configured destinations. Unknown keys, hostile
  URLs and foreign attempt references fail; setup/account attempts remain distinct.
- Replayed checkout creation uses the same session; cancelled/failed attempts can
  be retried intentionally. Forged success returns cannot unlock access; delayed
  and repeated webhooks preserve current idempotent billing behaviour.
- Migration from the previous release preserves existing users, profile fields and
  billing records; add coverage for new constraints and ownership boundaries.

### Risks and limits

The new document record is technical evidence of a submitted acceptance, not a
replacement for the app's published terms. Apps own document versions and URLs.
Stripe settlement can arrive after the browser returns; the contract must expose
that state instead of claiming immediate access. Local Maana currently pins DCM
3.13.0 while DCM is 3.15.0; its downstream adoption must inspect intervening changes.

## Part B — Implementation map (addressed to the implementer)

### Target working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (git root, branch `main`).
Use absolute paths. The checkout is shared: `src/django_core_micha/scripts/sync_secrets.py`
and `tests/test_sync_secrets.py` carry another session's uncommitted edits — do not open,
format or revert them. Other sessions may edit other files; preserve their edits.

### The contract is fixed — implement it exactly

The public contract is already written and is the authority for every name, status
code, body, check order and setting:

- **Terms acceptance:** `docs/design/terms.md` (whole file).
- **Contextual checkout and attempt resolution:** `docs/design/billing.md`, section
  "Contextual checkout and attempt resolution (DCM-AUTH-12)", plus the two new rows in the
  webhook table and the `context` sentence under "Stripe plan sync and payments".

Read both before writing code. Do NOT edit `docs/`, `CHANGELOG.md`, the `version` in
`pyproject.toml` or `WORK_ORDERS.md`. If the contract is contradictory or cannot be
implemented as written, stop with `RESULT: BLOCKED <the contradiction>` instead of
choosing an interpretation.

### 1. New opt-in app `django_core_micha.terms` (label `dcm_terms`)

Model the package on the opt-in `receipts` app; match the compact style of the file you
are mirroring.

- `src/django_core_micha/terms/__init__.py`, `apps.py` — `TermsConfig`, `name =
  "django_core_micha.terms"`, `label = "dcm_terms"`, `default_auto_field` BigAutoField;
  `ready()` imports `audit_config` and registers the system check (`dcm_terms.W001` for an
  absent `DCM_TERMS_DOCUMENT`, `dcm_terms.E001` for a malformed one). Pattern:
  `src/django_core_micha/receipts/apps.py:6-27`.
- `models.py` — `TermsAcceptance`: `user` FK to `settings.AUTH_USER_MODEL` (CASCADE,
  `related_name="terms_acceptances"`), `app` CharField(80), `version` CharField(64),
  `document_url` URLField(max_length=500), `accepted_at` DateTimeField(default=timezone.now);
  `UniqueConstraint(fields=("user","app","version"), name="dcm_terms_acceptance_unique")`.
  No other fields (no IP, no user agent).
- `migrations/__init__.py`, `migrations/0001_initial.py` — hand-written in the style of
  `src/django_core_micha/billing/migrations/0002_purchase_withdrawal.py`; it must match the
  model exactly (the Orchestrator verifies with `makemigrations --check`).
- `services.py` — `TermsDocument` (frozen dataclass `app, version, url`),
  `TermsNotConfigured`, `TermsVersionMismatch` (carries `.document`), `current_document()`
  (parses and validates `DCM_TERMS_DOCUMENT` on every call; malformed → `None`; share one
  validator with the system check), `has_accepted_current_terms(user)`,
  `accept_current_terms(user, version)` → `(acceptance, created)`. Idempotency under
  concurrency: create inside `transaction.atomic()`, and on `IntegrityError` return the
  existing row with `created=False`; never update an existing row's `accepted_at` or
  `document_url`.
- `permissions.py` — `HasAcceptedCurrentTerms(BasePermission)` with `message` and `code`
  `"terms_acceptance_required"`; anonymous → `False` (DRF then answers not-authenticated).
- `views.py`, `urls.py` — one plain Django view for `GET|POST <prefix>/` (route `""`,
  name `"dcm-terms"`), `@csrf_protect` + `require_http_methods(["GET","POST"])`,
  `Cache-Control: no-store` on every response, `JsonResponse` with key `detail` for errors,
  checks in exactly the order of the error table in `docs/design/terms.md`. Pattern:
  `src/django_core_micha/receipts/views.py:13-29` (but `detail`, not `code`).
- `audit_config.py` — `register(TermsAcceptance)`. Pattern:
  `src/django_core_micha/receipts/audit_config.py`.
- No admin registration, no middleware, no signal, no data migration, no change to
  `auth/models.py`, `auth/serializers.py` or `auth/views.py`.

### 2. Contextual checkout in `django_core_micha.billing`

- `src/django_core_micha/billing/models.py` (append after `Purchase`, line 48-60):
  `CheckoutAttempt` — `reference` CharField(64, unique), `user` FK CASCADE
  (`related_name="billing_checkout_attempts"`), `app` CharField(80), `plan_key`
  CharField(40), `context` CharField(40), `idempotency_key` CharField(64), `status`
  CharField(16, choices `open processing paid failed expired`, default `open`),
  `stripe_price_id` CharField(255), `stripe_session_id` CharField(255, blank),
  `stripe_checkout_url` TextField(blank), `expires_at` DateTimeField(null), `created_at`
  DateTimeField(default=timezone.now), `updated_at` DateTimeField(auto_now). Constraints:
  unique `("user","app","idempotency_key")` named `dcm_bill_attempt_key_unique`; unique
  `("stripe_session_id",)` with `condition=~Q(stripe_session_id="")` named
  `dcm_bill_attempt_session_unique`.
- `src/django_core_micha/billing/migrations/0003_checkoutattempt.py` — hand-written,
  depends on `("dcm_billing","0002_purchase_withdrawal")` and the swappable user model;
  creates only this model and its constraints.
- `src/django_core_micha/billing/audit_config.py:1-4` — add `CheckoutAttempt` to the
  registered tuple (its `stripe_*` and `reference` fields are then redacted by the existing
  rule).
- `src/django_core_micha/billing/config.py` — a loader/validator for
  `DCM_BILLING_CHECKOUT_CONTEXTS` returning the validated mapping (empty when absent or
  malformed) plus the error list the system check reports.
- `src/django_core_micha/billing/apps.py:264-271` — extend the existing registered check
  with `dcm_billing.E002` for a malformed contexts setting; `E001` unchanged.
- `src/django_core_micha/billing/stripe_service.py:117-135` `checkout_session_params` —
  add keyword-only optional `success_url=None, cancel_url=None, attempt_reference=None,
  context=None`. With all omitted the returned dict must be identical to today's (the
  existing test `billing/tests/test_review_fixes.py:76` pins it). With an attempt: the given
  URLs, `client_reference_id`, and metadata `checkout_attempt` / `checkout_context` (the
  metadata dict is shared with `payment_intent_data`; for subscriptions leave
  `subscription_data.metadata` as is).
- `src/django_core_micha/billing/views.py:111-129` `checkout` — keep the existing path
  byte-for-byte for a body without `context` (or `context: null`), including its error
  behaviour. Add the contextual branch per the contract: validate context, key, plan
  (`_latest_price`), then get-or-create the attempt in its own committed transaction
  (handle the `IntegrityError` race by re-reading), then in a second transaction lock it
  with `select_for_update()`, apply the reuse / not-open checks, return the stored session
  when present, else call `_stripe().checkout.Session.create(**params,
  idempotency_key=f"dcm-checkout:{app}:{reference}")` with the price id stored on the
  attempt, and store `id`, `url`, `expires_at`. The attempt row must be committed BEFORE the
  Stripe call, so that a failed or timed-out call keeps the reference (and with it the Stripe
  idempotency key) for the replay. Reference: `secrets.token_urlsafe(24)`. Destination
  URLs: append `checkout_attempt=<reference>` to the configured URL's query with
  `urllib.parse`, keeping existing parameters and any fragment.
- New view `checkout_attempt(request, reference)` and route
  `path("checkout/attempts/<str:reference>/", ..., name="billing-checkout-attempt")` in
  `src/django_core_micha/billing/urls.py:4`. Lookup filters `reference`, `user=request.user`
  and `app=DCM_BILLING_APP` together; every miss is the same 404. `no-store`.
- One serializer helper for the `{"url", "attempt"}` body shared by both views; the
  effective status reports `expired` for a stored `open` whose `expires_at` has passed, and
  `url` is non-null only for an effective `open` with a session.
- `src/django_core_micha/billing/stripe_service.py:311-389` `process_stripe_event` — after
  the existing app check (line 332-334) and before the existing branch logic, apply the
  attempt transition for the four `checkout.session.*` kinds named in the contract (lookup
  by session id, else by `metadata.checkout_attempt` + app + `user_id`, binding the session
  id; forward-only transitions). Add `checkout.session.async_payment_failed` and
  `checkout.session.expired` as handled kinds that return `"processed"` when an attempt
  changed and `"ignored"` otherwise. The existing grant/purchase logic and its return values
  for every other case stay unchanged. Do not add any Stripe API call to the webhook path.

### 3. Wiring for tests

- `tests/settings.py:3-21` — add `"django_core_micha.terms"` to `INSTALLED_APPS`.
- `pyproject.toml:51` — add `"src/django_core_micha/terms/tests"` to `testpaths`
  (`tests/test_testpaths_coverage.py` enforces it). Touch no other line of `pyproject.toml`.

### Required tests — files and patterns

Write the tests listed under "Acceptance and required tests to write" in Part A, in:

- `src/django_core_micha/terms/tests/__init__.py`, `.../terms/tests/test_terms.py`
- `src/django_core_micha/billing/tests/test_checkout_attempts.py`

Patterns to reuse: Stripe faked with `monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)`
and settings via `override_settings` (`billing/tests/test_review_fixes.py:76-97`); billing
tests run with CSRF disabled for `RequestFactory` by `billing/tests/conftest.py`, so the
terms CSRF test calls the view with a factory request that is NOT exempted and expects 403;
self-edit PATCH through `BaseUserViewSet` with a minimal serializer
(`tests/test_self_edit_delete_restriction.py:1-40`); migration preservation with
`MigrationExecutor` (`src/django_core_micha/notifications/tests/test_preference_matrix.py:58-75`:
migrate `dcm_billing` back to `0002_purchase_withdrawal`, create billing rows through the
historical models, migrate forward to `0003_checkoutattempt`, assert the rows and the user are
unchanged; for `dcm_terms` migrate from zero to `0001_initial`). Concurrency: assert the
`IntegrityError` path (a second `accept_current_terms`/attempt creation racing the first)
returns the existing row; SQLite in tests cannot run true parallel transactions.
Each assertion must fail if the behaviour were removed or stubbed (for example: a forged
`accepted_at` in the body returns 400 AND writes no row; a replayed key makes exactly one
`Session.create` call with the expected Stripe idempotency key; a forged success return
leaves `access_state` read-only and the attempt `open`).

Run only the tests you wrote, from the target working directory:
`python -m pytest src/django_core_micha/terms/tests src/django_core_micha/billing/tests/test_checkout_attempts.py -q -p no:cacheprovider`

### Invariants and pitfalls

- A checkout without `context` sends exactly today's Stripe parameters (no
  `client_reference_id`, no idempotency key) and answers exactly `{"url": ...}`.
- Browser input never chooses a URL, an app, a user, a timestamp or a status.
- No Stripe call in a GET view or in the webhook's attempt update.
- Webhook, `billing_reconcile` and grant idempotency (`grant_tokens`, `credit_purchase`,
  `_create_purchase`, `TeamPackage.get_or_create`) are not modified.
- Files are UTF-8: open them with explicit `encoding="utf-8"`.

### Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

Work from this package; do not explore broadly from scratch; open only the named files to
verify. Schema, authenticated writes and billing ARE in this WO's scope, exactly as mapped
above and nowhere else.

## Part C — Orchestrator only

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

### Execution directive

Configured `implementation` entry: `codex/gpt-6-luna` (`.claude/models.local.json`), CLI
route from the Claude Orchestrator: `codex exec -C <target dir> --skip-git-repo-check
--dangerously-bypass-approvals-and-sandbox -m gpt-6-luna -c model_reasoning_effort=high -
< work-orders/DCM-AUTH-12.md`, stdin closed by the file, output to the scratchpad.
`.claude/codex-status.md` had no line for 2026-10-21, so Codex is used by default.

Launch evidence: (filled after dispatch).

### Review routing

Tier 3: all `review.lenses` (envelope, regression, duplication, tests) plus `sec_review`,
concurrently, `codex exec --sandbox read-only -m gpt-6-luna`, diff inline, on the corrected
diff; `review_fallback` (claude/sonnet) only if Codex is unavailable. No `ui_review` (no
frontend in the diff). The Orchestrator runs its own security pass in parallel.

### Verification

`makemigrations --check` (no drift), then the affected set: `src/django_core_micha/terms/tests`,
`src/django_core_micha/billing/tests`, `tests/test_self_edit_delete_restriction.py`,
`tests/test_user_create_permission.py`, `tests/test_testpaths_coverage.py`,
`tests/test_auditlog.py`. Full suite is
not run locally (publish CI runs `pytest -q` before publishing).

Status: planned; Gate #1 approved by the operator on 2026-10-21 in the Maana
Expertenchat (session local_cbfc9d51-5217-42a4-99ba-27a315c854ce), for all three WOs
of the chain, delivery order unchanged. Implementation and the minor release of this
WO are authorized; consumer pins and production remain outside it.

After approval, follow `orchestrate-codex` and the current configured roles. Define
the API contract before mapping/dispatch; finish required independent Tier-3 review,
including security, on the corrected diff. Run the affected auth, billing and
migration tests. No frontend review is needed unless the approved scope changes.
Record the published version, contract reference and completed evidence in this
register row so UCM-AUTH-14 and MAANA-APP-19 can consume an actual release.
Commit only this WO's files on `main`; no consumer or production promotion is part
of this WO. Production app activation remains a later app promotion gate.
