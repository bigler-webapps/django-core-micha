# DCM-DX-10 — Generated local environment identity

## A. Envelope

Tier 3 — shared configuration generation and environment isolation.

The canonical generator emits ENV_TYPE for every selected supported environment, including
local. Its value comes from the selected environment, not secret inputs, app_env or overrides.
Retain staging/production/edge identities and all existing isolation guards. A missing or
unknown declared environment still refuses. No agent reads or writes an actual environment
file; tests generate only synthetic output into non-env-named temporary files. No real secret
provider, network call, schema, app auth, test-management guard relaxation or dependency change.

Write focused tests that call the real generate_env entry point with fixture config and
mocked secret inputs: local is emitted once, non-local identities remain correct, conflicting
input cannot override identity, undeclared environment refuses. Preserve existing generator
tests. The Orchestrator owns versioning, release and local runner updates.

## B. Implementation map

Workspace root: `C:/Users/Micha Bigler/Documents/webapps`.
Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.
Own only `src/django_core_micha/scripts/generate_env.py`, existing related generator tests if
needed, and a focused `tests/test_generate_env_identity.py`. The current function conditionally
omits ENV_TYPE when env_name is local; its later add/input loop owns precedence. Inspect those
seams and existing fixture/mocking conventions. No edits to unrelated assistant changes,
register, WO, version metadata or governance files.

### Implementer preamble and progress contract

First tool action: `codex-hook-canary-ws-gov-60`. Expected denial means continue. This WO is the
complete specification. You are a writing implementer, not reviewer or orchestrator. You are
not alone in the codebase: preserve others' changes and accommodate them. Edit only the named
source/tests. Run only your newly written tests once. No installs, package managers, git,
registers, memory, real env files, secret access, self-review or additional agents. Use English
source/comments. Print PLAN, bounded PROGRESS and final RESULT: DONE or BLOCKED with evidence.

## C. Orchestrator only

STOP — implementer instructions end here.

This is the shared-core part of the operator-approved WS-GOV-94 package. Independent review,
affected tests, patch release and the required local runner update belong to the Orchestrator.

## Gate record

Gate #1 approved by the operator on 2026-11-18 in the current workspace chat, explicitly
accepting correction of the generated local environment identity.

## Completion

The generator emits the configured environment identity before merging input keys.
Local now explicitly selects the pre-existing dev path, including log-only invitation
and recovery mail. Production, staging and edge generation and the isolation guards
retain their existing behavior. A local identity alone does not pass test-management
activation, which still checks its enable flag, original identity and isolation proof.

`test_generate_env_identity.py`, `test_generate_env.py` and
`test_test_management_isolation.py`: 21 passed. First-pass findings prompted tests
that prove all input channels were exercised and reject undeclared environments for
the right reason. The closing independent review returned no findings on all five
lenses. Terra covered Envelope and security, Luna covered regression, duplication
and tests. The implementer ran on Luna and left the changes uncommitted. The package
patch version is 3.33.2. Release and local runner updates are verified separately.
