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
