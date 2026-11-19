# DCM-DX-12 — Declared PostGIS database image for isolated test management

## Part A — Envelope

Tier 3 — shared-core isolation, local infrastructure and privileged test-controller boundary.

### Outcome

An application whose database needs PostGIS (hram: `django.contrib.gis.db.backends.postgis`, geometry fields in `scenario/models.py`) can adopt the isolated local test center. Today the shared starter accepts only the database image `postgres:18`, so such an app cannot start an instance. The consumer declares its database flavour explicitly; the starter validates it against a fixed allowlist and keeps every other isolation guarantee unchanged.

### Scope and boundaries

- Extend the consumer declaration (`test-management.consumer.json`, version 1) with an optional, validated database flavour with exactly two allowed values: the existing plain PostgreSQL (default, current behaviour and image `postgres:18`) and one fixed PostGIS image with an exact pinned tag chosen by the implementer from hram's own compose (`postgis/postgis:18-3.6` is the current hram local image; confirm it is a published tag and record it). No free-form image string, no registry or digest from the declaration, no other database engines.
- Bind the flavour into every place that today hardcodes `postgres:18`: manifest validation, runtime inspection of the running containers, resume, stop, the browser relay and the data-directory mount checks (the PostGIS image keeps its data under the same `/var/lib/postgresql` layout, verify before relying on it). A mismatch between declaration, manifest and running container fails closed before Docker mutation, as for any other image mismatch.
- Existing consumers (Innoservice legacy, Maana, the synthetic fixture) keep working unchanged without a declaration change; the synthetic fixture stays plain PostgreSQL.
- Preserve all existing safety checks: local original environment, local Docker endpoint, two-service layout, internal network, read-only code and frontend mounts, isolated volumes, no extra ports, mounts, privileged mode or Compose extensions, loopback controller and relay, handle verification, run ownership, cleanup. The database healthcheck keeps its fixed form unless the PostGIS image needs a different, equally strict one; justify any change.
- Out of scope: how an application provides engine source, data or model directories inside the two-service layout (HRAM-SEC-7 decides that on the application side, or returns with a separate need), Redis/Celery replacement (app settings), any hram change, version bump beyond the release below.
- Document the new declaration field and its allowed values in `docs/design/test-management.md`. Release a backward-compatible minor version; verify it is available on the index the consumers actually use before HRAM-SEC-7 pins it.

### Risks

A looser image check weakens the isolation boundary; the allowlist must be closed and exact. PostGIS images may differ in mount layout, entrypoint, user or healthcheck behaviour. Existing instances started before the change must stay valid.

### Required tests to write

1. Declaration parsing: default, each allowed flavour, unknown value, wrong type, extra keys, and an image string smuggled through the declaration are rejected before Docker is contacted.
2. Manifest and runtime validation for both flavours: matching accepted; flavour/image mismatch between declaration, manifest and running container, tag drift, digest-style or foreign images rejected; start, resume, stop and relay all use the same check.
3. Compatibility: Innoservice legacy instances, a Maana-style consumer and the synthetic fixture unchanged; existing test-management suites stay green. Include a negative test that a copied PostGIS declaration cannot make a synthetic fixture or a non-declaring app eligible.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`; start at `C:/Users/Micha Bigler/Documents/webapps`. `src/django_core_micha/scripts/isolated_test_start.py` hardcodes `postgres:18` at runtime inspection (around lines 194 and 258) and in the manifest validator (around lines 326 and 341-346 for env and healthcheck); data mount `/var/lib/postgresql` is checked around lines 223, 232, 273 and 322. `src/django_core_micha/test_management/local_proxy.py` repeats identity and image assumptions for the relay. `src/django_core_micha/test_management/{isolation,activation,isolated_entrypoint,local_bridge}.py` own the marker and controller boundary; do not weaken them. `scripts/run_dev.py` owns startup invocation. Existing focused tests: `tests/test_test_management_{launcher,isolation,safety,http_relay,bridge}.py` and `tests/test_test_management.py`. Reference consumers: `innoservice/docker-compose.test-management.yml`, `maana/docker-compose.test-management.yml` (database service at its `db:` block, `postgres:18`), `maana/test-management.consumer.json`. Contract text: `docs/design/test-management.md` ("Consumer declaration"). Preserve the strict checks rather than replacing them with a permissive parser. Orchestrator owns version metadata, packaging, dependency installation and release; the implementer edits only source, focused tests and the contract doc.

### Verified seams (Orchestrator, 2026-11-19, against HEAD c2ccde0)

Absolute target root: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha/src/django_core_micha/`.

- `scripts/isolated_test_start.py`: `_load_consumer` (line 36) parses the declaration with a closed field set (`{"version","identity"}` plus optional `mode`); it returns `(cwd, identity, synthetic_local)`. `consumer()` (line 83) returns `(cwd, identity)` and is called by `test_management/local_proxy.py` (lines 20, 104, 157) and in this file (lines 535, 580). Do not change those return arities in a way that breaks existing callers or tests; add the database flavour as a new explicit, validated value reachable from the same loaded declaration (for example a keyword on the validators defaulting to the plain PostgreSQL flavour, resolved from the declaration by the callers).
- Hardcoded `postgres:18`: `validate_runtime` (line 194), `validate_stopped_instance` (line 258), `validate_compose_file` (line 326, `db["image"]`). Callers of those three: this file (lines 473, 483, 510, 537, 588, 591, 630) and `local_proxy.py` (106-107, 159, 164). Every call site must pass the same flavour; a forgotten site must fail closed, not default silently to a looser value.
- Existing negative rules to keep and extend: a `synthetic-local` declaration must stay plain PostgreSQL (the synthetic fixture in `scripts/synthetic_test_fixture.py` lines 51-63 is plain and validated by `validate_fixture`); the `innoservice` identity/legacy no-declaration path stays plain PostgreSQL; a declaration that adds the flavour to either must be rejected.
- Data mount and healthcheck are asserted at lines 223, 232, 273, 322, 346 and in `test_management/isolation.py` lines 85-86. The PostGIS image derives from `postgres:18` (same `/var/lib/postgresql` layout, `pg_isready` present); confirm from the repository evidence you can read and keep the healthcheck unchanged unless a concrete reason is recorded in the doc.
- Image tag: `postgis/postgis:18-3.6` is the hram local image (`hram/docker-compose.yml` line 231) and the tag exists on Docker Hub (HTTP 200, checked 2026-11-19). Use exactly that tag; the declaration carries a flavour name, never an image string.
- Tests live in `tests/test_test_management_{launcher,isolation,safety,http_relay,bridge}.py` and `tests/test_test_management.py`; follow their existing fake-`inspect` style.

### Implementer preamble and progress contract

Your first tool action is the exact command `codex-hook-canary-ws-gov-60`; the expected denial is `WS-GOV-60 canary: workspace hooks are active`. Continue after it; if absent, report unverified coverage. This file is the COMPLETE specification. You are the IMPLEMENTER, not the orchestrator. Work only on approved source, tests and the contract doc. Run only tests you wrote, once. Do not install dependencies, edit version metadata or lockfiles, run package managers, edit registers or memory, launch other agents, review, stage, commit or push. Report blockers without expanding scope. Start with `PLAN: ...`, emit `PROGRESS: [n/total] ...` before relevant actions and on completion, and finish `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — implementer instructions end here.

Origin: found while mapping HRAM-SEC-7 (2026-11-19): hram cannot adopt the test center because the starter accepts only `postgres:18`. Independent Tier-3 security and general review are mandatory before landing; the shared-core push to `main` needs no major-version approval if the release is a minor. Coordinate with the open test-management work (MAANA-APP-44 follow-ups) and check `webapp-management/SHARED_CAPABILITIES.md`. After the release, HRAM-SEC-7 pins it and decides engine/data provisioning.

## Gate record

Created at the operator's request on 2026-11-19 ("DCM-WO anlegen"). Gate #1 approved by the operator 2026-11-19 ("DCM-DX-12 bigge angehen, freigabe erteilt"); Envelope unchanged since creation.
