# Local test-management API

The Innoservice integration runs only in a separately created local instance.
It uses dedicated database and media volumes, an internal Docker network, and
an instance-bound controller. Normal development and deployed settings do not
mount this API.

`django_core_micha.test_management` is an opt-in Django app. A consumer adds it to
its dedicated disposable local settings and mounts `django_core_micha.test_management.urls`
under an application-owned prefix. It is intentionally absent from
`django_core_micha.api_urls` and from ordinary development settings.

Every request requires a local-loopback `Authorization: Bearer <controller token>`
header. Cookie authentication, browser `Origin`/`Referer` requests, remote addresses,
non-local original environments, and incomplete isolation evidence are refused. The
token is compared using an instance-bound digest and is never returned by the API.

Startup verifies the original local environment, Docker resources, database and
media mounts, and per-boot evidence. Requests repeat the relevant isolation checks.
The controller credential remains in the container; the local client sends requests
through a restricted stdin bridge. Browser traffic uses a separate loopback proxy,
which does not expose the management routes or the Django admin.

The API exposes discovery, exclusive idempotent runs, run-owned account CRUD,
named fixtures, reset, recovery, and closure. Responses contain opaque handles and
requested/effective state; passwords and controller credentials are never returned.
The adapter owns application relationships, real permission evaluation, domain
fixtures, and cleanup of its run-scoped records.

## Consumer declaration

An application opting in supplies `test-management.consumer.json` at its
repository root, for example `{"version": 1, "identity": "example-app"}`.
The identity is a lowercase ASCII slug of at most 30 characters. The identity
`innoservice` is reserved for existing Innoservice checkouts; these continue to
work without a declaration and retain their existing handles. Existing instances
using the legacy image remain valid. Newly built images, including Innoservice,
use a name derived from the resolved checkout directory, so identical slugs in
different directories do not share images. Other applications must provide the
declaration.

The optional `build_args` object contains at most eight literal string entries,
for example `"build_args": {"INSTALL_ENGINE": "0"}`. Names must match
`^[A-Z][A-Z0-9_]{0,39}$` and values `^[A-Za-z0-9._-]{1,40}$`.
Names containing `TOKEN`, `SECRET`, `KEY`, `PASSWORD`, `PASSWD`,
`CREDENTIAL`, or `AUTH`, beginning with `TEST_MANAGEMENT_`, `DOCKER_`, or
`COMPOSE_`, or equal to `HTTP_PROXY`, `HTTPS_PROXY`, or `NO_PROXY` (any case)
are reserved. Innoservice and synthetic-local fixtures cannot declare this
field. The backend build mapping must contain exactly these literal arguments;
an empty or absent object requires the original build mapping without `args`.
Secrets, targets, extra contexts and environment-sourced arguments are not supported.
Non-empty arguments add a hash of their sorted canonical JSON to the checkout-scoped
image name; existing names remain unchanged without arguments. Runtime checks use
this name as evidence of the declared build arguments, since Docker image config
does not record build arguments. Restore the original declaration and manifest
to manage an instance built with different arguments.

The optional `database` field is `postgresql` (the default, image
`postgres:18`) or `postgis` (image `postgis/postgis:18-3.6`). For example:
`{"version": 1, "identity": "example-app", "database": "postgis"}`.
No image names or other database values are accepted. Innoservice and the
synthetic-local fixture cannot declare this field. The manifest and running
container must match the declaration for every lifecycle and relay operation,
including stop. An instance started with another flavour can be stopped again
after the declaration is restored.
The starter verifies the running database image against the declaration before
signing the per-boot isolation proof. A PostGIS proof includes the signed
`postgis` flavour and requires Django's `django.contrib.gis.db.backends.postgis`
engine. Plain proofs omit the flavour and require
`django.db.backends.postgresql`; existing plain proofs remain valid. Settings or
environment values cannot select the proven flavour, and mismatches are refused.
The PostGIS tag is the hram local image, confirmed on Docker Hub on
2026-11-19. Its [Dockerfile](https://github.com/postgis/docker-postgis/blob/master/18-3.6/Dockerfile)
uses the PostgreSQL 18 base; it retains the `/var/lib/postgresql` volume layout
and `pg_isready`, so the existing mount and healthcheck contract is unchanged.

A fixed disposable synthetic consumer can be created with
`python -m django_core_micha.scripts.synthetic_test_fixture`. This prints the path
of a fresh operating-system temporary directory. Run `run-dev --test-management`
from that directory. The image builds independently from `python:3.13-slim`, with
explicit Django/psycopg/Daphne versions and only the required named source files
from the core package that generated the fixture. No Innoservice checkout, app
image or credentials are required. The initial image build needs registry/package
access; the running database/backend network remains internal. This is a
launcher/capture smoke test, not an application adoption shortcut.
Its `synthetic-local` declaration, complete source, settings, manifest and directory
inventory are checked against the core's generated contract on every lifecycle
operation and relay request. Copied declarations in existing applications,
modified fixture sources and unexpected files/directories are rejected before
Docker is contacted. This versioned declaration contains no secrets. The consumer root must have no
`.env` path at all, including a link. Its verified Compose manifest omits
`env_file` and sets `ENV_TYPE: local` literally. Implicit Compose environment-file
loading is disabled and inherited `COMPOSE_ENV_FILES` is removed. The remaining service keys,
runtime resources, mounts, images, network, database and disposal checks stay
under the same strict contract. The launcher still rejects an inherited
`ENV_TYPE` of `staging`, `production` or another non-local value, then removes
the inherited value before Compose runs. Real-app declarations and legacy
Innoservice manifests retain the `.env` and interpolated `ENV_TYPE` contract;
the synthetic-local mode cannot be used by Innoservice. Start, resume, stop and
the browser relay validate this same declaration and manifest contract.

Stopping is a narrower recovery operation: it validates the declaration, the local
Docker endpoint and the actual running instance's checkout/image/project, bind
sources, volumes, network and port isolation before stopping its two exact
container names. It does not parse current Compose files or execute current app
source. A missing frontend build or changed core fixture source therefore cannot
prevent stopping an otherwise verified owned instance. Start, resume and relay
continue to require the full current source/manifest contract.

The application owns its isolated Compose file, dedicated Django settings,
adapter, fixtures, and controller client. Its `docker-compose.test-management.yml`
must use the strictly verified two-service layout shown by the Innoservice
manifest, including the same isolated volumes, read-only code and frontend
mounts, local-only environment and entrypoint. For a new consumer, replace the
backend image value with `${TEST_MANAGEMENT_IMAGE:?missing isolated image}`;
the starter supplies the directory-bound name. The frontend build must exist
at `frontend/build_current/index.html`. Source paths and the manifest must
resolve inside the consumer directory. The shared starter rejects unknown
manifest keys and does not support arbitrary Compose extensions.

Each instance handle contains the declared identity and a random UUID. Start,
resume, stop, and the browser relay verify the handle against the declaration,
the Compose working-directory labels, source bind mounts, image and isolated
resources. A consumer cannot use another checkout's instance; stopping retains
its owned volumes for explicit recovery. Only a local Docker daemon is accepted.
An application must provide its own domain adapter and test fixtures: declaring
a consumer does not automatically grant it one or enable management routes in
ordinary application settings.

## Innoservice workflow

From the Innoservice repository, start `run-dev --test-management --no-log-stream`.
Save the printed non-secret instance handle and loopback URL. In a Windows cmd
session in `innoservice/frontend`, supply those two outputs to the tests:

```cmd
set TEST_MANAGEMENT_PROJECT=<instance-handle>
set TEST_MANAGEMENT_BASE_URL=<ready-url>
npx playwright test tests/test-management-api.spec.js tests/test-management-profiles.spec.js tests/test-management-workflows.spec.js --project=desktop --workers=1
```

The client in `frontend/tests/test-management-client.js` supports discovery,
run and account lifecycle, fixtures, ordinary CSRF-protected login, and opaque
handles for independent browser contexts. Generate synthetic credentials in memory;
do not print them, cookies, or browser authentication state. Each test closes its
run in a `finally` block. Tests disable trace, screenshots, and video.

Stop a completed instance with `run-dev --test-management --test-management-stop
<instance-handle>` from the repository root. Its dedicated volumes persist. Resume
the same stopped instance with `run-dev --test-management --test-management-resume
<instance-handle> --no-log-stream`. A run interrupted by a restart requires
explicit recovery before further changes; recovery verifies owned resources.
Stopping or resuming does not delete volumes. Local results do not constitute
staging acceptance and do not bypass application MFA.

## Local password recovery

DCM 3.34.0 extends the declared DCM-DX-8 consumer contract with a private,
one-time way for the trusted test client to retrieve a reset link from the
isolated local instance. The application still generates and validates the
ordinary reset link and applies its normal password policy and MFA flow.

When test management is enabled, the rendered reset message is captured at the
local delivery boundary only when its user primary key matches exactly one
active account owned by the active run in the current isolated instance. The
message stores only a handle and link; subject and body are not retained. A
newer reset for that account discards the prior link. No email is sent outside
the isolated instance.

The trusted client claims the message through the controller bridge with
`POST /api/test-management/runs/<run>/accounts/<account>/recovery-message/`.
The response contains `handle` and `link` once. Missing, claimed, discarded,
closed, wrong-run and non-owned messages use the same `recovery_message_not_found`
response. The controller route remains loopback-only and rejects browser-originated
requests; the browser relay continues to block all `/api/test-management` paths.

For a password-recovery test, the client starts a run and creates an account
through the app's adapter, then requests recovery from the app's real signed-out
password-reset UI using that account's synthetic address. The trusted test client
claims the link, keeps it and the reset token in memory, and completes the app's
own confirmation form. The user then signs in through the normal app UI with the
new password. The management API never impersonates the user or sets a password
directly.

The link, token and passwords must stay in trusted client memory. Do not print or
persist them in logs, test output, evidence, screenshots, traces or videos, even
when a request or assertion fails. Clear the client state when the run closes.
The server blanks and discards the stored link on claim, run reset/close, account
deletion and run recovery after restart. In an enabled test center, capture
failure leaves the ordinary non-enumerating reset response unchanged and never
falls back to logging the URL. With test management disabled or absent, ordinary
local behavior is unchanged.
