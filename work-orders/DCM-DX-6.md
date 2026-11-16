# DCM-DX-6 — Local test-management API with Innoservice as first consumer

## Part A — Envelope

### Goal and expected outcome

An Orchestrator can prepare an isolated local Innoservice test environment, create
synthetic accounts with different effective permissions, sign them in through the
ordinary authentication API, execute multi-account workflows, and clean up its
test run without asking the operator to create accounts or perform browser logins.

The reusable test-management API belongs to django-core-micha (dcm). Innoservice
supplies the first application adapter and executable integration scenarios. This
is one feature spanning the two repositories, with this file as its canonical WO.

### Scope and tier

- Tier 3: shared-core code, privileged account provisioning and permission changes.
- Primary repository: `django-core-micha`, trunk `main`.
- First consumer: `innoservice`, trunk `develop`.
- Included: opt-in local test-management API, application adapter contract,
  synthetic account/profile lifecycle, run-owned fixtures and cleanup, local
  bootstrap/client tooling, API documentation, and affected automated tests.
- Included where necessary: dedicated local test settings/storage configuration,
  an isolated-test mode in the existing `run-dev` startup path, opt-in test-module
  persistence/migrations, and Innoservice's exact dcm dependency pin for this
  additive public capability. No unrelated dependency updates.
- No new product UI or ucm component; no prototype or visual redesign.

### Local-only boundary and bootstrap

1. The capability is disabled by default and is absent from ordinary application
   route registration. Activation requires an explicit dedicated local-test
   configuration, positively identified disposable storage, and separate
   test-controller authorization. `DEBUG`, a hostname or a single copied flag
   alone must not authorize activation.
2. Staging, production and unknown environments refuse activation and all
   management operations, including when a caller supplies a valid controller
   credential or incorrectly enables an individual test setting. The original
   environment identity must not be hidden by a test-settings override.
3. The first version operates on a separate local database and media storage,
   using synthetic data only. Cache, sessions and background work must not share
   mutable state with ordinary development or another application instance.
   Existing development databases, staging/production databases and customer
   data copies are not valid targets. Failure to establish isolation is a refusal,
   not permission to reuse the default database.
4. The local bootstrap provides a documented agent-callable entry point through
   the existing `run-dev` flow. It establishes the isolated instance and the
   controller authorization without a human browser login. Startup returns
   non-secret instance/run identifiers and readiness information.
5. Controller authorization is ephemeral and bound to the local test instance.
   A normal application session, including a synthetic application administrator,
   does not grant test-management access. Local reachability alone is insufficient.
   Browser-originated requests must not obtain this authority through ambient
   cookies or permissive CORS. Remote exposure is unsupported and refused.
6. Bootstrap/client processes handle controller credentials and synthetic account
   passwords privately; secrets, cookies and authentication-state contents do not
   enter model-visible output, URLs, logs, source control or evidence reports.
   No agent reads or edits `.env`; any application process loads only its own
   repository's configuration under the existing workspace rules.
7. One active run owns an instance exclusively in this first version. A competing
   run receives a clear conflict rather than sharing or resetting the active
   run. A crashed run remains identifiable and can be explicitly recovered by
   its authorized controller without silently taking over another live run.

### Management API and application adapter

1. Expose machine-readable operations to inspect available profile templates and
   allowed configuration, create/inspect a run, create/list/inspect/update/delete
   its synthetic accounts, prepare named fixtures, reset the run to its declared
   baseline, and close the run. Report effective profile/permission state as well
   as requested state so that an agent can verify preparation succeeded.
2. Templates are conveniences, not a closed set of fixed accounts. The controller
   can create additional identities and supported role/capability combinations
   without editing source or asking the operator for each account. Supported
   fields and capabilities are explicitly advertised and validated by the adapter;
   arbitrary model writes, executable expressions and caller-selected imports
   are not part of the API.
3. dcm owns controller/run authorization, lifecycle conventions, account ownership
   and the adapter protocol. Innoservice owns its domain relationships and fixture
   semantics. Another consumer can implement the protocol without importing
   Innoservice models or copying the controller API.
4. All management targets are bound to the authorized instance and run. A supplied
   database ID, username prefix, email match or claimed ownership flag is never
   sufficient to adopt an existing account or object. Reject edits/deletions of
   non-owned accounts and fixtures, and cross-run references.
5. Repeating the same logical creation request is idempotent; conflicting reuse
   fails explicitly. Invalid profiles and adapter failures leave no half-created
   account or partial role assignment. Cleanup errors are surfaced with a
   recoverable state; they are not reported as successful resets.
6. Account deletion, reset and run closure revoke the affected authenticated
   sessions and dispose of private client authentication artifacts. A recreated
   account cannot inherit an old authenticated session. Cleanup covers owned
   domain rows, files and test-specific transient state without touching unrelated
   records or storage.
7. Organisation-wide policy fixtures are allowed only inside the exclusively
   owned disposable instance. Their setup/reset must be explicit and restore the
   declared baseline. Deleting every row in an existing database is not a valid
   implementation of run cleanup.
8. The client exposes opaque account/session handles to the Orchestrator and
   supports opening independently authenticated browser contexts and making
   API requests as a selected test identity. It provides structured errors and
   redacted evidence suitable for unattended execution.

### Authentication and permission semantics

- Use the existing regular login API and its normal session/CSRF behavior. No
  frontend login is required for ordinary workflow tests. No impersonation header,
  fabricated user context, direct session injection or general authentication
  bypass is introduced by the management API.
- The controller's provisioning authority is separate from each test person's
  ordinary permissions. Positive and negative UI/API checks execute as that
  person, never with the controller credential attached to application requests.
- Preserve the normal account activation and authentication-factor policies.
  Baseline workflow profiles use a supported password-login configuration in the
  isolated instance; stronger-factor scenarios are not silently bypassed or
  reported as covered. A normal login refusing a profile is an explicit result.
- Changing a role or capability exercises Innoservice's real permission checks.
  Rights-withdrawal acceptance includes the already authenticated session; logging
  in again is not a substitute for that assertion.

### Innoservice first-consumer scope

- Supply templates for application administrator, supervisor, normal collaborator,
  collaborator in another company, read-only collaborator, room-management profile
  and low-privilege profile, with inspectable effective grants and supported
  variations using the existing capability registry.
- Create the necessary user profile, collaborator, organisation membership, role
  and company relationships consistently. Preserve the singleton-organisation
  invariant: another company is not another organisation or tenant. Different
  company membership must not be assumed to deny an otherwise permitted project.
- Provide minimal synthetic projects/places and document data for the acceptance
  scenarios below. Keep unrelated business workflows and permission semantics
  unchanged.
- Reuse the existing API-login and Playwright context patterns. Keep the current
  local/CI E2E suite working. Do not expose or call the existing globally destructive
  `seed_e2e` command as the new API's reset implementation, and preserve its
  staging/production refusal.
- Document a single reproducible agent workflow for startup, profile discovery,
  account/fixture provisioning, ordinary login, multi-account verification and
  cleanup. Both scripted assertions and agent-directed browser checks can use it.
- Report local evidence as local evidence. Existing staging acceptance cases and
  release gates are not closed merely because their local equivalents pass.

### Required tests and acceptance outcomes

Write or extend focused tests for the following outcomes:

1. **Activation boundary:** a correctly bootstrapped isolated local instance works.
   Normal local development, staging, production, unknown environments, missing
   isolation evidence and mismatched configuration refuse before mutation. A valid
   credential cannot override those refusals. Verify the unenabled app has no
   usable management routes and remote/untrusted-origin requests are refused.
2. **Controller boundary:** missing, invalid, expired and other-instance controller
   authorization fails. An ordinary user and an application administrator cannot
   manage test identities through their application sessions.
3. **Account lifecycle:** create, inspect, update and delete each initial profile;
   create an additional supported rights combination; verify effective grants and
   relationships. Invalid fields, capabilities, cross-run references and attempts
   to adopt non-owned accounts fail without partial mutation. Retry/idempotency
   and adapter-error paths have positive controls.
4. **Run lifecycle:** competing runs conflict; controlled recovery is explicit;
   reset/close remove only owned data/files and invalidate old sessions. Sentinel
   unrelated rows/files remain unchanged. Repeat reset and retry interrupted
   cleanup with a truthful result and no duplicate accounts.
5. **Real login and isolation:** provision at least two identities, sign both in
   using the regular API without browser-form input, and open independent browser
   contexts. Verify server-side identity for each and that logout of one does not
   sign out the other. Verify cleanup prevents reuse of both old sessions.
6. **Permissions:** exercise allowed and forbidden reads/writes through direct API
   calls as well as relevant browser paths. Include read-only, room-management,
   low-privilege and other-company cases with expectations derived from the real
   Innoservice policy. Include affected permission/security inventory tests.
7. **Withdrawal:** a collaborator performs an allowed write; the controller changes
   the run-owned rights fixture; the same still-authenticated collaborator retries
   and the server refuses without creating data. Restoring the grant restores the
   allowed path. Other profile expectations remain explicit and verified.
8. **Shared workflow:** account A changes a synthetic project's document layout;
   account B observes the saved setting and the actual generated PDF uses it.
   A second synthetic project retains its own layout. This is a local counterpart
   to INNO-QA-2 case 16-02, not a claim of staging or physical-device acceptance.
9. **Private media:** authorized access succeeds; a low-privilege identity where
   the real policy denies access and an unauthenticated context cannot obtain the
   protected fixture. The authorized session remains usable.
10. **Unattended operation and regression:** the documented workflow runs from
    bootstrap through cleanup without operator login/account creation. Output and
    failure evidence are redacted. Relevant existing login, seed-guard and
    multi-account tests retain their behavior. No full unrelated suite is required.

### Non-goals and risks

No staging/production activation, remote test controller, customer-data import,
generic admin console, ucm UI, all-app rollout, arbitrary impersonation, CI workflow
change, production deployment, general permission redesign or exhaustive workflow
coverage. No physical-device, SSO or MFA acceptance claim. No governance edits.
No changes to business-model schemas; any new persistence is opt-in test-module
state only. No global relaxation of login, CSRF, permission or secret-handling rules.

Principal risks are activation against the wrong database, leakage of controller
authority into user tests, stale sessions after cleanup, inconsistent app-specific
role relationships, and organisation-wide rights changes contaminating other work.
The isolation, ownership, authorization and negative tests above are required
outcomes of this feature, not optional hardening.

## Part B — Implementation map

### Context package and implementation seams

- In dcm, add an opt-in `src/django_core_micha/test_management/` Django app,
  including its adapter protocol, isolated-instance and controller checks,
  ownership persistence/migrations, lifecycle service and API routes/serializers.
  Keep it outside default application registration and `api_urls.py`. Add focused
  tests under `tests/test_test_management*.py` and the app's test configuration.
- Extend `src/django_core_micha/scripts/run_dev.py` (`main`, compose selection and
  readiness) and `tests/test_run_dev.py` for the isolated local startup entry point.
  Document its contract and controller/client usage in a new dcm documentation
  file. The Orchestrator owns compose/infra-lane edits, dependencies and release.
- In Innoservice, add the adapter under `backend/core/` and dedicated local-test
  settings under `backend/backend/`; register the dcm routes only there via
  `backend/backend/urls.py`. Add focused tests under `backend/core/`. Existing
  `e2e_settings.py` records original environment before overriding it; do not
  treat its override as proof of isolation. `seed_e2e.py` has useful examples of
  role and fixture construction but its global reset must never be called here.
- Domain seams: `core/access_control.py` resolves the singleton organisation and
  effective grants; `core/capability_registry.py` defines supported capabilities;
  `people/models.py` defines collaborator, company, organisation, role and
  membership relationships; `users/models.py` creates a profile on user creation.
  Keep exactly one organisation and use real permission checks.
- Add a private local client and Playwright scenarios in `frontend/tests/` using
  `e2e-helpers.js` as the regular CSRF/login pattern, and update
  `docs/api-contract.md`. The client must keep credentials and authentication state
  out of model-visible output and separate controller requests from app sessions.
  Existing `admin-role-tool-right.spec.js` and `delivery-note-layout.spec.js` show
  relevant rights and PDF workflows. No product frontend changes are in scope.
- Contract: expose discovery, exclusive run creation/inspection/recovery, owned
  accounts CRUD, named fixtures, reset and closure through structured JSON and
  opaque instance/run/account/session handles. Use explicit idempotency keys on
  logical creation and clear conflict/validation/authorization errors. Adapter
  input is a validated template plus advertised role/capability fields, never
  model names, arbitrary imports or expressions. Return requested and effective
  grants and relationships without returning credentials. The client supplies
  passwords privately and performs ordinary CSRF-protected login.
- API schema (all endpoints below `/api/test-management/`, only in dedicated
  settings): `GET discovery/` returns instance ID, templates, supported account
  fields/capabilities and fixture names; `POST runs/` accepts `idempotency_key`
  and baseline declaration and returns a run handle/status; `GET runs/{run}/`
  inspects it; `POST runs/{run}/recover/`, `/reset/` and `/close/` take explicit
  intent and return truthful lifecycle/cleanup status. `GET`/`POST
  runs/{run}/accounts/` list/create; `GET`/`PATCH`/`DELETE
  runs/{run}/accounts/{account}/` inspect/change/remove. Create accepts
  `idempotency_key`, template, synthetic identity, password and advertised
  configuration; reads return handle, requested configuration and effective
  grants/relationships, never password. `POST runs/{run}/fixtures/` accepts an
  idempotency key, advertised fixture name and validated parameters, and returns
  a run-owned opaque fixture handle. All errors carry stable `code` and `detail`;
  conflicting keys or active runs return 409, invalid payloads 400, unauthorized
  controllers 401/403, and unowned or cross-run handles 404. No credentials in
  URLs, logs, error details or persisted plaintext. Final field-level validation,
  fixture parameters and response schemas must be documented from the adapter's
  advertised contract alongside the implementation.
- Verify every activation and operation against original environment, disposable
  instance and isolated database/media/cache/session/background configuration;
  do not rely on DEBUG or a flag alone. Controller authority is ephemeral,
  instance-bound, non-cookie, unavailable to browsers and restricted to local
  reachability. Tests must exercise refusal before mutation, ownership and recovery.
- Work from these seams; open the named files to verify details. Preserve unrelated
  existing files and foreign untracked data. The Envelope governs all acceptance
  outcomes and is the authority for scope.

Absolute targets:

- `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`
- `C:/Users/Micha Bigler/Documents/webapps/innoservice`

All writing implementers start from
`C:/Users/Micha Bigler/Documents/webapps` and use these absolute target paths.

### Implementer preamble

> This committed WO is the complete specification. Read the nearest governance,
> relevant engineering skill and app MEMORY.md for conventions. Stay within the
> mapped scope. Edit source and test files and run only the tests you wrote, once;
> a tests-only implementer runs none. Do not install dependencies, modify lockfiles,
> run package managers, tidy unrelated changes, read or edit .env, update MEMORY.md,
> edit the WO register, self-review, or run git add/commit/push. Leave the diff for
> the Orchestrator's independent reviews and authoritative tests. Report blockers
> rather than expanding scope. Start with PLAN, emit PROGRESS at relevant steps
> with no gap longer than approximately two minutes, and finish with exactly one
> RESULT: DONE or RESULT: BLOCKED line.

## Part C — Orchestrator only

> STOP if you are the implementing instance. Everything below belongs to the
> Orchestrator, not to you. Do not launch reviewers or another implementer, perform
> final verification, edit register outcomes, or commit/push.

### Authoring evidence and context anchors

Authoring date: 2026-11-16. dcm orientation succeeded on `main` at `01a3942`;
no outgoing commits or working changes existed before authoring this WO.
Innoservice was inspected locally on `develop`; its earlier orientation failed
at fetch and reported extensive foreign files. Refresh and check overlap before
implementation; the app observations are not certified as current remote HEAD.

Use these bounded anchors to build Part B, with line numbers refreshed then:

- dcm `src/django_core_micha/auth/models.py:1`: shared abstract profile conventions;
  `auth/permissions.py`, `auth/roles.py`: existing authorization/profile seams.
- dcm `src/django_core_micha/scripts/run_dev.py:1`: sanctioned local startup;
  `pyproject.toml:1`: current packaging, public CLI and test setup.
- Innoservice `AGENTS.md`: singleton organisation invariant.
- Innoservice `backend/core/management/commands/seed_e2e.py:1`: existing synthetic
  profiles, role matrices and fixtures; its table-wide deletion is NOT reusable
  for management API cleanup.
- Innoservice `backend/backend/e2e_settings.py:1`: original environment check and
  test configuration; `backend/core/test_seed_e2e.py` and `test_e2e_settings.py`:
  existing safety tests.
- Innoservice `backend/core/access_control.py:70`, `backend/core/capability_registry.py`
  and `backend/people/models.py:131`: effective permissions and domain membership.
- Innoservice `frontend/tests/e2e-helpers.js:1`, `reseed.js:1`,
  `admin-project-access.spec.js:1`, `admin-role-tool-right.spec.js:1`,
  `delivery-note-layout.spec.js:1`: login, current reset and multi-context patterns.
- Innoservice `work-orders/INNO-QA-2.md`, Missing acceptance evidence: 16-02,
  16-10, 17-01..05 and 17-06 motivate this local test capability. Do not change
  those staging results or existing approved scopes under this WO.
- `webapp-management/SHARED_CAPABILITIES.md`: existing shared authentication and
  local startup capabilities; no test-management API was identified during scoping.

### Execution and review

After Gate #1, follow `orchestrate-codex`, refresh model routing and finish Part B
before any dispatch. Ordinary shared-core/application initial implementation uses
the configured implementer, with the documented fallback if unavailable. Any
necessary local compose/startup configuration follows the infra-lane authorship
and review rules. No CI or remote infrastructure change is authorized here.

Run the configured independent envelope, regression, duplication, tests and
security reviews on the assembled scoped diffs; perform the Orchestrator's own
permission/exposure check. No product frontend is planned, so no visual prototype
gate applies; follow the tier rules if an actual frontend implementation enters
scope. Browser test code alone does not authorize a new product UI.

Use the existing standing authorization for configured OpenAI read-only reviews;
state WO, actual model and scoped non-secret payload at launch. Secrets and real
personal/customer data never belong in review input. No review was run or claimed
at WO-authoring time.

Run affected dcm tests, app security/permission tests and the named multi-account
Playwright scenarios, locally with one worker. Record current source/version and
isolated-instance identity, each acceptance outcome, redacted evidence and cleanup
result. Scripts must supply credentials without printing them. No operator browser
login, account preparation or screenshots are prerequisites for ordinary local
verification. An unavailable tool is a blocker to report, never evidence of a pass.

Keep this one canonical WO for the cross-repository package. Record consumer
commits here; do not create a competing specification in Innoservice. Publish the
additive dcm capability through its normal release process and update Innoservice's
exact pin to that release. Follow normal repository reviews/tests and trunk rules.
Source adoption does not enable the API on staging: verify that refusal explicitly.
No promotion PR or production action is included.

At completion update the register using `wo.py` and record commits, reviewer
identities, evidence, remaining limitations and durable discoveries here. Do not
mark done with only the shared API landed and the Innoservice adapter still pending.

## Gate record

2026-11-16: The operator requested creation of this WO for dcm with Innoservice as
first consumer, initially limited to an isolated local test environment. This
authorizes WO authoring/publication only. Gate #1 implementation approval is pending.

2026-11-16: Gate #1 approved by the operator by requesting implementation of this
WO; the request was reaffirmed after the GitHub access interruption.

## Completion

In progress; not released. The isolated PostgreSQL/media stack started without a
published backend port. Browser checks passed for all seven profiles and an extra
rights combination, two independent logins, real permission changes, private media,
both PDF layouts, reset/session revocation and explicit restart recovery. Local
activation checks rejected staging, production and unknown environment identities;
this is not a staging deployment acceptance test. The targeted core suite passed
86 tests, the consumer adapter/manifest suite 28 and the existing permission suite
48. Independent two-round reviews ran on core and consumer; in-scope findings were
fixed and retested. The installed-package check, release and consumer deployment
remain outstanding. A credential-like database value appeared in an earlier test
traceback and was reported for rotation; no credential is recorded here.
