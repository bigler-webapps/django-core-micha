# DCM-DX-8 — Declared consumer support for isolated test management

## Part A — Envelope

Tier 3 — shared-core isolation, local infrastructure and privileged test-controller boundary.

### Outcome

The existing disposable local test-management starter, controller bridge and browser proxy support any application that explicitly adopts a documented shared consumer contract, including Maana and future apps, without hardcoded per-application branches. An app remains responsible for its own domain adapter and fixtures; generic infrastructure must not pretend to understand an application's domain automatically. Existing Innoservice consumers and their running/start/stop/resume behavior continue to work.

### Scope and boundaries

- Replace the fixed Innoservice directory, instance-name and image assumptions with a validated, explicit consumer identity and shared declaration. Bind startup, compose validation, runtime evidence, proxy, lifecycle and cleanup to the same identity and exact instance. Provide a clear consumer contract so future compatible apps can opt in without a new shared-core release or app-specific code inside core.
- Preserve or strengthen all existing safety checks: original environment must be local; no unknown or malformed declaration, path escape, project/image mismatch, remote Docker endpoint, unexpected mount, volume, network, host port, process, role or controller can activate the API. Maintain internal isolated networking and distinct database/media/session/cache/background resources. Fail closed before migration, provisioning or proxy exposure. Do not inspect or print secrets, `.env` contents, external container environments or Compose-expanded configuration; the application may read its own process configuration for its existing isolation checks without printing it.
- Preserve Innoservice's existing published contract, instance handle format where applicable, normal login/CSRF, controller isolation, run ownership, concurrency, lifecycle, reset, close, recovery and cleanup guarantees. Never adopt, delete or modify another app's or another run's resources. Only the explicitly targeted disposable local instance may be managed.
- Document the consumer contract and security assumptions. Release a backward-compatible shared-core version and update the local runner only as needed to use it. Maana integration and its dependency pin are handled under MAANA-APP-44 after this release; no unrelated app or production infrastructure changes are included.

### Required tests

Write focused launcher, manifest, runtime-inspection, proxy and lifecycle tests covering two independently declared synthetic consumers plus existing Innoservice compatibility; start/resume/stop, instance and resource ownership, collision and cross-app rejection, malformed or missing declarations, path and identity spoofing, remote Docker, non-local/unknown environments, invalid or absent isolation evidence, unexpected ports/mounts/networks/volumes and fail-closed behavior before migration. Include security tests for controller/browser separation, no leaked secrets or administrative routes, and evidence that another app can adopt the documented declaration without modifying core source. Verify the actual `run-dev` and acceptance-runner integration with a disposable synthetic app, with no real customer data, external provider calls or production credentials.

## Part B — Implementation map

Start at `C:/Users/Micha Bigler/Documents/webapps`; target `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Shared owner repo is `main`. `src/django_core_micha/scripts/isolated_test_start.py` currently hardcodes `innoservice-tm-`, the Innoservice directory and image in start/resume/stop and manifest/runtime validation. `src/django_core_micha/test_management/local_proxy.py` repeats the identity/directory assumptions. `src/django_core_micha/test_management/{isolation,activation,isolated_entrypoint,local_bridge}.py` implement the existing marker, controller and process boundaries; `scripts/run_dev.py` owns startup invocation. Existing focused tests include `tests/test_test_management_{launcher,isolation,safety,http_relay,bridge}.py` and `tests/test_test_management.py`. Existing consumer reference: `innoservice/docker-compose.test-management.yml`, `innoservice/backend/backend/local_test_management_settings.py`, and `docs/design/test-management.md`. Preserve the strict checks, rather than replacing them with a permissive configuration parser. Orchestrator owns version metadata, packaging, dependency installation and release; implementer may edit only source, focused tests and contract docs.

### Implementer preamble and progress contract

Your first tool action is the exact command `codex-hook-canary-ws-gov-60`; the expected denial is `WS-GOV-60 canary: workspace hooks are active`. Continue after it; if absent, report unverified coverage. This file is the COMPLETE specification. You are the IMPLEMENTER, not the orchestrator. Work only on approved source, tests and contract docs. Run only tests you wrote, once. Do not install dependencies, edit version metadata or lockfiles, run package managers, edit registers or memory, launch other agents, review, stage, commit or push. Report blockers without expanding scope. Start with `PLAN: ...`, emit `PROGRESS: [n/total] ...` before relevant actions and on completion, and finish `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — implementer instructions end here.

The operator explicitly approved authoring and implementation of the shared-core dependency, required Maana/local-runner version updates and generalization for future apps on 2026-11-17. Preserve the APP-44 isolation requirements. Use the configured implementation worker and independent Tier-3 security/general review. Publish the appropriate non-major release and verify it is actually available before consumer adoption. Do not alter governance scripts or bypass local-container and secret rules. The existing APP-23 CI exception is recorded separately; do not claim its visual acceptance has passed.

## Gate record

Gate #1 approved by the operator on 2026-11-17: “Ja, darfst du, aber so dass dcm dann mit allen neuen App umgehen kann”.
