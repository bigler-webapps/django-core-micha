# DCM-DX-16 · Support pgvector for isolated local acceptance

## A. Envelope

Enable Cockpit's real isolated acceptance for CP-CI-2 under WS-GOV-112. The existing KB migrations require the PostgreSQL vector extension. Add the declared database flavour `pgvector` bound to the app's existing `pgvector/pgvector:pg18` image. Keep the verified disposable network, storage, loopback and controller boundaries. The feature applies only to the local declared consumer contract.

Tier 3, shared-core and isolation security. Retain compatibility for the existing PostgreSQL and PostGIS flavours and proofs. Preserve the exclusions on legacy and standalone synthetic consumers. Product authorization, Django migrations, production settings, delivery, and permissive image overrides are outside scope.

The runner must enforce the declaration and exact image on start, resume, relay, and stop. The bootstrap proof must authenticate the observed database flavour and only allow the corresponding Django backend. Pgvector uses Django's standard PostgreSQL backend. Reject an incorrect image, flavour, proof, or engine before enabling management. Exercise matching and mismatched declarations, runtime image verification, post-boot verification and activation, and old PostgreSQL and PostGIS proofs through the supported Core runner. Verify the released capability with CP-CI-2 before claiming Cockpit acceptance.

## B. Implementation map

Absolute target `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Extend the existing PostGIS contract in `src/django_core_micha/scripts/isolated_test_start.py` around the declared consumer image map, the verified bootstrap and resume path. Check the relay and stop paths use the same resolver. Map proof handling in `src/django_core_micha/test_management/isolation.py`, `isolated_entrypoint.py` and `activation.py`. Existing contract tests are `tests/test_test_management_consumers.py` and `tests/test_test_management_postgis_proof.py`. Document the declared flavour in `docs/design/test-management.md`.

Named writable paths are the four Core source files above, `docs/design/test-management.md` and a new focused `tests/test_test_management_pgvector_proof.py`. Refer to the test files above for shared fixtures. The Orchestrator owns `project.yaml`, git, the register, release steps and evidence runs. The DCM-DX-15 diagnosis edits in the launcher around `_DIAGNOSTIC_PATTERNS` and `_run_private` belong to another change. The operator authorized disjoint pgvector hunks in the same working tree. Preserve every diagnosis hunk and stop if an edit overlaps them.

### Implementer preamble

> You are the configured implementer for DCM-DX-16 only. The parent checked the paths and assigns the named hunks to this run. Run `codex-hook-canary-ws-gov-60` first and report unverified coverage if the expected denial is absent. This WO is the complete specification. Write only the named Core paths in the absolute target. Other sessions share the checkout. Preserve their changes. Never read environment files, credentials or real records. Do not install packages, change version pins, alter the register, start stacks, review, delegate, stage, commit or push. The Orchestrator runs tests. Return an uncommitted diff with PLAN, PROGRESS, and RESULT.

## C. Orchestrator only

Record the scoped Core test results, independent envelope, regression, duplication, tests and security review, release compatibility, and the eventual Cockpit acceptance result. Keep the Core and app results traceable separately.

## Gate record

Gate #1 approved by the operator 2026-11-21 (chat approval for fixed pgvector support and existing isolation proof coverage). The operator also approved disjoint edits alongside the uncommitted DCM-DX-15 diagnostic and register work.

## Completion

Implementation commit `7ae4894`. `tests.py` passed 117 isolation tests and 10 launcher tests on the corrected tree. The first independent pass found one missing negative activation assertion. The closing envelope, regression, duplication, tests and security lenses reported no findings. Codex used gpt-6.1-terra for envelope, regression, tests and security and gpt-6-luna for duplication. The release and Cockpit evidence are tracked by DCM-CI-3 and CP-CI-2.

Released in 3.41.2. The published launcher accepted Cockpit's pgvector declaration and named a disposable instance. Compose then failed to start its containers, so CP-CI-2 has no isolated browser pass. Keep that verification open.
