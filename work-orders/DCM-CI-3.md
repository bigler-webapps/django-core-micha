# DCM-CI-3 · Prepare and publish Core 3.41.2

## A. Envelope

Release the independently reviewed pgvector support from DCM-DX-16 as Core 3.41.2. The full backend suite requires the repository's existing `reimbursements` test extra. Align CI and the publish workflow with the tested dependency set. Preserve the existing triggers, job requirements, packaging and publish conditions. Consume the exact release in Cockpit under CP-CI-2 once it is available.

Tier 3, CI and shared-core release. Verify the installed test dependencies, the backend and isolation scopes, and publication through the existing main-branch workflow. Verify Cockpit separately through its isolated acceptance protocol. Release only reviewed Core changes. The existing outgoing DCM-DX-15 work-order commit may ride along as expressly approved by the operator.

## B. Implementation map

Target `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. In `.github/workflows/ci.yml` and `.github/workflows/publish.yml`, ensure the existing test-extra install lists include `reimbursements`. Inspect both lines before editing. Update the release version from 3.41.1 to 3.41.2 in `pyproject.toml` and add a concise release entry in `CHANGELOG.md` for DCM-DX-16 and the test dependency alignment.

### Implementer preamble

> You are the configured implementer for DCM-CI-3. The parent checked this increment's named paths. Run the WS-GOV-60 canary and report unverified coverage if its denial is absent. Edit only the two workflow files, pyproject.toml and CHANGELOG.md in the named checkout. Preserve all parallel edits. No environment files, credentials, real records, dependency installations, test commands, stacks, review, register edits or Git writes. Leave an uncommitted diff and emit PLAN, PROGRESS and RESULT.

## C. Orchestrator only

Record independent release review, named test evidence, version, landing and publish result. The DCM-DX-15 diagnostic implementation and other working changes belong to their owners.

## Gate record

Gate #1 approved by the operator 2026-11-21 (chat approval for the CI test extra, Core 3.41.2, inclusion of planning commit bf6d243, and the eventual Cockpit pin).

## Completion

Release preparation commit `a1a9c0d` adds the test extra to Core CI and sets 3.41.2. The publish workflow already installs that extra. The full supported Core backend run passed 1,999 tests with eight skipped after installing the declared extras. The DCM-DX-16 focused run passed 117 isolation and 10 launcher tests. Independent release review reported no findings across envelope, regression, duplication, tests and security, using codex/gpt-6.1-terra and codex/gpt-6-luna. Publication and Cockpit acceptance are pending.

The [publish workflow](https://github.com/bigler-webapps/django-core-micha/actions/runs/44946878268) completed successfully at `3e0f5ce`. The published 3.41.2 package installed in a dedicated local runtime. Cockpit's local image reports 3.41.2. CP-CI-2 remains open because its isolated Compose startup did not reach a ready URL.
