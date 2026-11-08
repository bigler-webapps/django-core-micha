# DCM-CI-2 — Install reimbursement test dependencies in the publish workflow

## Part A — Envelope

Tier 3 (CI). The release test job must install the existing `reimbursements` extra so that
the reimbursement rendering and excerpt tests run with their declared dependencies.
Add that extra to the existing editable-install list in `.github/workflows/publish.yml`.
Keep workflow triggers, permissions, version detection, test command and publishing unchanged.
This fixes the release gate for BCO-EXP-3; it does not change application behavior or dependencies.
Risk: a broken release gate can prevent publication or omit meaningful tests.
Tests: run the reimbursement and dependency-guard tests with the declared extra installed;
inspect the workflow change and its syntax. Operator approved the workflow addition on 2026-11-08.

## Part B — Implementation map

The orchestrator owns this infra-lane change. Repository: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.
The publish workflow's `Install and run tests` step installs extras defined in `pyproject.toml`.
The `reimbursements` extra supplies the libraries used in `tests/test_reimbursements_render.py`.
Do not touch foreign changes in `src/django_core_micha/scripts/sync_secrets.py` or
`tests/test_sync_secrets.py`.

## Part C — Orchestrator only

STOP — the following is for the orchestrator. Independently review the actual workflow diff with
the configured Tier-3 infra review routes. Record review and scoped test evidence in the register.
The approved release remains a separate version change and is verified after publication.
