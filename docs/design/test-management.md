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
