# DCM-DX-13 — Declared build arguments for isolated test-management images

## Part A — Envelope

Tier 3 — shared-core isolation, local infrastructure and privileged test-controller boundary.

### Outcome

An application whose production Dockerfile needs a build argument to produce a local, secret-free image (hram: `INSTALL_ENGINE=0`, because its private engine pin needs a build secret that the isolated test center must never carry) can adopt the isolated test center. Today the shared starter fixes the backend build to exactly `{"context": ".", "dockerfile": "backend/Dockerfile"}`, with no arguments, target or secrets, so such an app cannot produce an isolated image and its production Dockerfile default stays untouched. The consumer declares a small, validated set of build arguments; the starter passes exactly those and keeps every other isolation guarantee.

### Scope and boundaries

- Extend `test-management.consumer.json` (version 1) with an optional `build_args` object of at most 8 string entries. Names match `^[A-Z][A-Z0-9_]{0,39}$`; values match `^[A-Za-z0-9._-]{1,40}$`. Reject names that look like credentials (contain `TOKEN`, `SECRET`, `KEY`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `AUTH`), names starting with `TEST_MANAGEMENT_`, `DOCKER_`, `COMPOSE_`, and proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, any case). No secrets, no build secrets, no build target, no `ssh`, no extra contexts, no values read from the environment or from `.env`. Reject duplicate keys and any other type before Docker is contacted.
- The Compose manifest's backend `build` must equal exactly `{"context": ".", "dockerfile": "backend/Dockerfile"}` when the declaration has no `build_args` (unchanged), and exactly the same mapping plus `"args"` equal to the declared arguments when it has some. A manifest whose args differ from the declaration, are extra, missing or reordered into another form fails closed. The starter builds with the declared arguments only; inherited `BUILD_*`-style environment variables never alter them.
- The image name already derives from the resolved checkout directory; it must additionally depend on the declared arguments (stable hash), so the same checkout with other arguments never shares an image, and an existing instance's image stays valid. Start, resume, stop and the browser relay verify the declared arguments against the manifest and the running image (label or equivalent evidence of the build arguments that produced it, set by the starter at build time, not trusted from the image alone if a stronger check exists).
- Existing consumers (Innoservice legacy, Maana, the synthetic fixture, any consumer without the field) keep working unchanged. Innoservice and the synthetic fixture cannot declare `build_args`.
- Preserve all existing safety checks: local original environment, local Docker endpoint, two-service layout, internal network, read-only code and frontend mounts, isolated volumes, no extra ports, mounts, privileged mode or Compose extensions, loopback controller and relay, handle verification, run ownership, cleanup, and the `database` flavour from DCM-DX-12.
- Out of scope: any hram change (HRAM-SEC-7 adopts the release afterwards), build secrets, multi-stage target selection, changing the shared synthetic fixture, a version bump beyond the minor release below.
- Document the field, the name/value rules and the reserved names in `docs/design/test-management.md`. Release a backward-compatible minor version; verify availability on the index the consumers use before HRAM-SEC-7 pins it.

### Risks

Build arguments widen what an app can steer during an image build; a loose allowlist or a credential-shaped name could smuggle secrets into image layers. A mismatch between declaration, manifest and image could let a stale image run. Image-name changes must not orphan or invalidate existing instances.

### Required tests to write

1. Declaration parsing: absent field (unchanged), valid arguments, too many, bad names (credential-like, reserved prefixes, proxies, lowercase), bad values, wrong types, duplicate keys, extra keys, and the field on Innoservice/synthetic declarations are rejected before Docker is contacted.
2. Manifest and runtime validation: matching args accepted; missing, extra or changed args rejected; start, resume, stop and relay use the same check; the image name differs for different args and is stable for equal args; existing no-args images and instances remain valid.
3. Build invocation: the starter passes exactly the declared arguments and nothing from the inherited environment; a test proves a credential-shaped inherited variable never reaches the build. Compatibility with existing suites and a negative test that a copied declaration cannot make a synthetic fixture or a non-declaring app eligible.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`; start at `C:/Users/Micha Bigler/Documents/webapps`. `src/django_core_micha/scripts/isolated_test_start.py`: declaration parsing and the `database` field around lines 54-69 (extend the allowed key set and validation here), `_image_matches` at 128 and `consumer_image(identity, cwd)` used at 339, 569 and 620 (add the args hash), runtime inspection at 217 and 282, the manifest validator around 326-348 (the backend `build` equality is at 340), and the build/start invocation near 569-620. `src/django_core_micha/test_management/local_proxy.py` repeats identity and image assumptions for the relay. `src/django_core_micha/test_management/{isolation,activation,isolated_entrypoint,local_bridge}.py` own the marker and controller boundary; do not weaken them. `scripts/run_dev.py` owns startup invocation. Existing focused tests: `tests/test_test_management_{launcher,isolation,safety,http_relay,bridge}.py`, `tests/test_test_management.py` and the DX-12 database-flavour tests as the pattern. Reference consumers: `innoservice/docker-compose.test-management.yml`, `maana/docker-compose.test-management.yml`; contract text `docs/design/test-management.md` ("Consumer declaration", the `database` paragraph). Preserve the strict checks rather than replacing them with a permissive parser. Orchestrator owns version metadata, packaging, dependency installation and release; the implementer edits only source, focused tests and the contract doc.

### Implementer preamble and progress contract

Your first tool action is the exact command `codex-hook-canary-ws-gov-60`; the expected denial is `WS-GOV-60 canary: workspace hooks are active`. Continue after it; if absent, report unverified coverage. This file is the COMPLETE specification. You are the IMPLEMENTER, not the orchestrator. Work only on approved source, tests and the contract doc. Run only tests you wrote, once. Do not install dependencies, edit version metadata or lockfiles, run package managers, edit registers or memory, launch other agents, review, stage, commit or push. Report blockers without expanding scope. Start with `PLAN: ...`, emit `PROGRESS: [n/total] ...` before relevant actions and on completion, and finish `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — implementer instructions end here.

Origin: found while mapping HRAM-SEC-7 (2026-11-19): after DCM-DX-12 (dcm 3.38.0) the next blocker is that the starter fixes the backend build to a fixed mapping, so hram's engine-free local image cannot be produced. Independent Tier-3 security and general review are mandatory before landing, with attention on credential-shaped names and image provenance. A minor release does not need version-specific approval. Check `webapp-management/SHARED_CAPABILITIES.md` and coordinate with other open test-management work. After the release, HRAM-SEC-7 pins it, adds guarded engine imports so `collectstatic` runs without `hram_3_engine`, and continues.

## Gate record

Created at the operator's request on 2026-11-19 ("B: DCM-DX-13"), as option B of the HRAM-SEC-7 build-contract finding. Gate #1 pending: the operator has not yet approved this Envelope.
