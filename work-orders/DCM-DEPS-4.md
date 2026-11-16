# DCM-DEPS-4 — Publish the approved registration and ledger capabilities

## Part A — Envelope

**Target:** django-core-micha, `main`.

Tier 3 — shared-core release metadata.

### Goal and scope

Publish the already landed, independently reviewed DCM-BILL-12 and DCM-AUTH-15 public capabilities so MAANA-APP-23 can consume a fixed released version. Increase the package's own version from 3.26.2 to 3.27.0, reflecting the new opt-in public interfaces. Do not alter source, other dependencies, workflows or runtime behavior. The Maana dependency pin and deployment remain governed by MAANA-APP-23 and its gates.

### Tests and risks

Verify that the package metadata declares the intended version and that the existing automated publish workflow's tests, build and publication succeed. The release includes all previously landed main-branch changes, so consumers must evaluate their own pin changes and regression scope.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. The only release edit is `[project].version` in `pyproject.toml`. This is orchestrator-owned release metadata; no feature implementer is dispatched. Preserve unrelated work and use the configured independent Tier-3 review.

## Part C — Orchestrator only

STOP — implementer instructions end here. The operator requested completion of the approved shared-core dependencies and MAANA-APP-23, including release sequencing. No major version or manual workflow dispatch is included. Follow the existing push-triggered publish workflow and report its actual outcome.

## Gate record

The operator approved implementing the separately scoped Shared-Core work and continuing MAANA-APP-23 on 2026-11-16; publishing the necessary minor package version follows that approved work and the workspace's normal shared-core release flow.
