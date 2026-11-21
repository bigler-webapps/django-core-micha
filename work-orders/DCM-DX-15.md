# DCM-DX-15 · Redacted isolated-starter diagnostics

## A. Envelope

### Goal and outcome

When the isolated test-management starter cannot start its containers, provide a useful fixed diagnostic category without exposing the child process output, configuration, environment names or values, credentials, paths, or command lines. Use the resulting supported diagnostic to investigate the blocked BCO-CI-1 isolated start.

### Scope and non-goals

Extend only the existing isolated launcher diagnostic classification and its focused tests. Preserve its fail-closed isolation, process execution, timeouts and cleanup. Do not disclose raw process output or broaden permissions, change consumer configurations, dependencies, CI, secret handling or production application behavior. No environment files may be inspected by the agent.

### Tier, prerequisites and risks

Tier 3: shared core and sensitive test isolation. The main risk is leaking sensitive child output or emitting a misleading cause. Categories must be fixed strings matched to specific observable error forms; unknown errors remain generic. BCO verification requires the approved launcher to be available through the supported installed runtime.

### Required tests

Cover recognized Compose failure forms, arbitrary unknown failures, output split across stream chunks, and hostile synthetic strings containing fake variable names, values, paths and credentials. Assert no raw output or sensitive fragments are printed and existing diagnostics and cleanup remain intact.

## B. Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Work in `src/django_core_micha/scripts/isolated_test_start.py` around `_DIAGNOSTIC_PATTERNS` and `_run_private`, with focused tests in `tests/test_test_management_launcher.py` or a cohesive new test module. The existing reader buffers chunks and emits only fixed messages; preserve that trust boundary and use no environment-file access. Report any needed path expansion.

### Implementer preamble

> You are the dispatched implementer, not the Orchestrator. First run `codex-hook-canary-ws-gov-60` from the workspace root; report an unexpected result and continue. This WO is the complete specification. Preserve all concurrent changes. Read no environment files or secrets. Edit only the named source and focused tests. Do not install dependencies, alter lockfiles or registers, operate stacks, review, delegate, stage, commit or push. Run only newly authored tests at most once. Leave an uncommitted diff and report PLAN, PROGRESS and RESULT.

## C. Orchestrator only

STOP if you are the implementer. The Orchestrator owns the register, supported test runs, independent general/security review, release and BCO diagnosis. Keep this WO open on an evidence or runtime blocker.

## Gate record

Gate #1 approved by the operator 2026-11-21 (chat: approved the limited redacted DCM starter change and BCO diagnosis).

## Completion

Pending.
