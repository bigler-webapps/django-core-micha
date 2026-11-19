# DCM-DX-14 — PostGIS database engine in the test-management activation check

## Part A — Envelope

Tier 3 — shared-core isolation, local infrastructure and privileged test-controller boundary.

### Outcome

An application that declared `"database": "postgis"` (DCM-DX-12, dcm 3.38.0) can pass the per-request activation check of the isolated test center. Today `validate_configuration()` in `src/django_core_micha/test_management/activation.py` (line 56) requires the Django database engine to be exactly `django.db.backends.postgresql`, so an app whose models need `django.contrib.gis.db.backends.postgis` (hram: geometry fields in `scenario/models.py`) is refused with `test_management_isolation_unverified` on every controller request, after the starter, image build, PostGIS container and migrations all succeeded. Found while running HRAM-SEC-7 live (2026-11-19): discovery returned 403 for this reason; migrations had run only because the app settings use the PostGIS engine.

### Scope and boundaries

- Accept exactly one additional engine, `django.contrib.gis.db.backends.postgis`, and only for an instance whose declared flavour is `postgis`. The flavour must come from trusted instance evidence, not from application settings alone: the starter already knows the declaration; propose and implement the narrowest way to hand it to the container check that the controller can verify (for example a value the starter passes into the instance environment and the per-boot proof, validated against the running image evidence), and fail closed when flavour, engine and database image disagree. A plain-flavour instance must keep requiring `django.db.backends.postgresql`; a postgis engine on a plain instance and a plain engine on a postgis instance are both refused.
- The closed allowlist stays closed: no other engine, no free-form engine string, no change to host, port, user, database name, mount, data-directory or `pg_read_file` evidence checks. Check every other place that compares the engine or the image (`isolation.py`, `local_bridge.py`, `isolated_entrypoint.py`, the starter) and keep them consistent.
- Existing consumers (Innoservice legacy, Maana, the synthetic fixture, any plain declaration) behave exactly as before; instances started before the change stay valid.
- Preserve all existing safety checks and the DX-12/DX-13 contract. Document the rule in `docs/design/test-management.md`. Release a backward-compatible minor version and verify it on the index consumers use before HRAM-SEC-7 pins it.
- Out of scope: any hram change, other databases, version bump beyond the minor release.

### Risks

Relaxing the engine check weakens a boundary that guards against running the controller against a non-disposable database; the flavour binding must be unforgeable from application settings. Mismatched flavour/engine combinations must fail closed.

### Required tests to write

1. Activation: plain flavour with plain engine accepted; postgis flavour with postgis engine accepted; postgis engine on plain flavour, plain engine on postgis flavour, any other engine string and a missing or forged flavour value refused, each with the existing generic refusal that exposes no configuration.
2. Starter/instance wiring: the flavour reaches the check from trusted evidence for both flavours; a forged environment or setting value does not widen the check; existing evidence checks (host, port, user, name, mounts) still fail as before.
3. Compatibility: Innoservice legacy, Maana-style and synthetic consumers unchanged; existing test-management suites stay green.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`; start at `C:/Users/Micha Bigler/Documents/webapps`. `src/django_core_micha/test_management/activation.py`: `validate_configuration()` lines 35-73, the engine comparison at line 56 inside the per-request check; `isolation.py` `verify_marker` and `validate_database_evidence` (lines 81-89, data directory and mountinfo); `isolated_entrypoint.py` and `local_bridge.py` (instance bootstrap, proof file, per-boot evidence); `src/django_core_micha/scripts/isolated_test_start.py` (`_DATABASE_IMAGES`, declaration loading, `_finish_start`, the environment handed to the instance, around lines 38-100 and 580-700). Existing tests: `tests/test_test_management_{launcher,isolation,safety,http_relay,bridge}.py` and `tests/test_test_management.py`; the DX-12 database-flavour tests are the pattern. Reference consumers: `maana/backend/backend/local_test_management_settings.py`, the hram adoption in `hram/backend/backend/local_test_management_settings.py` (uses the PostGIS engine). Preserve strict checks rather than replacing them with a permissive parser. Orchestrator owns version metadata, packaging and release; the implementer edits only source, focused tests and the contract doc.

### Implementer preamble and progress contract

Your first tool action is the exact command `codex-hook-canary-ws-gov-60`; the expected denial is `WS-GOV-60 canary: workspace hooks are active`. Continue after it; if absent, report unverified coverage. This file is the COMPLETE specification. You are the IMPLEMENTER, not the orchestrator. Work only on approved source, tests and the contract doc. Run only tests you wrote, once. Do not install dependencies, edit version metadata or lockfiles, run package managers, edit registers or memory, launch other agents, review, stage, commit or push. Report blockers without expanding scope. Start with `PLAN: ...`, emit `PROGRESS: [n/total] ...` before relevant actions and on completion, and finish `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — implementer instructions end here.

Origin: third shared-core gap found while adopting the test center for HRAM-SEC-7 (after DCM-DX-12 and DCM-DX-13): the activation check hardcodes the plain PostgreSQL engine. Independent Tier-3 security and general review are mandatory before landing, with attention on the flavour binding being unforgeable from app settings. A minor release does not need version-specific approval. After the release HRAM-SEC-7 pins it, upgrades the global dcm for `run-dev`, and continues the live run.

## Gate record

Created at the operator's standing request to unblock HRAM-SEC-7 ("B: DCM-DX-13" covered the build-argument gap; this is the next gap of the same adoption). Gate #1 pending: the operator has not yet approved this Envelope.
