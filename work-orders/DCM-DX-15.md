# DCM-DX-15 · Redacted isolated-starter diagnostics

## A. Envelope

### Goal and outcome

When the isolated test-management starter cannot start its containers, provide a useful fixed diagnostic category without exposing the child process output, configuration, environment names or values, credentials, paths, or command lines. Use the resulting supported diagnostic to investigate the blocked BCO-CI-1 isolated start.

### Scope and non-goals

Extend only the existing isolated launcher diagnostic classification and its focused tests. Preserve its fail-closed isolation, process execution, timeouts and cleanup. Do not disclose raw process output or broaden permissions, change consumer configurations, dependencies, CI, secret handling or production application behavior. No environment files may be inspected by the agent.

The operator additionally approved fixed, redacted categories for errors during the isolated database migration, to diagnose Cockpit and Musiknoten. Unknown errors remain generic; never emit child output, secret-like fragments, identifiers or environment names. Preserve the pre-existing uncommitted DCM-DX-15 changes in the same files.

### Tier, prerequisites and risks

Tier 3: shared core and sensitive test isolation. The main risk is leaking sensitive child output or emitting a misleading cause. Categories must be fixed strings matched to specific observable error forms; unknown errors remain generic. BCO verification requires the approved launcher to be available through the supported installed runtime.

### Required tests

Cover recognized Compose failure forms, arbitrary unknown failures, output split across stream chunks, and hostile synthetic strings containing fake variable names, values, paths and credentials. Assert no raw output or sensitive fragments are printed and existing diagnostics and cleanup remain intact.

Also cover recognized migration failure forms and unknown migration failures using synthetic outputs, including split chunks and hostile sensitive-looking text; assert that only fixed categories are emitted and pre-existing launch, cleanup and isolation behavior remains intact.

## B. Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Work in `src/django_core_micha/scripts/isolated_test_start.py` around `_DIAGNOSTIC_PATTERNS` and `_run_private`, with focused tests in `tests/test_test_management_launcher.py` or a cohesive new test module. The existing reader buffers chunks and emits only fixed messages; preserve that trust boundary and use no environment-file access. Report any needed path expansion.

### Implementer preamble

> You are the dispatched implementer, not the Orchestrator. First run `codex-hook-canary-ws-gov-60` from the workspace root; report an unexpected result and continue. This WO is the complete specification. Preserve all concurrent changes. Read no environment files or secrets. Edit only the named source and focused tests. Do not install dependencies, alter lockfiles or registers, operate stacks, review, delegate, stage, commit or push. Run only newly authored tests at most once. Leave an uncommitted diff and report PLAN, PROGRESS and RESULT.

## C. Orchestrator only

STOP if you are the implementer. The Orchestrator owns the register, supported test runs, independent general/security review, release and BCO diagnosis. Keep this WO open on an evidence or runtime blocker.

## Gate record

Gate #1 approved by the operator 2026-11-21 (chat: approved the limited redacted DCM starter change and BCO diagnosis).

Gate #1 expansion approved by the operator 2026-11-21 (chat: redacted migration diagnostic categories; preserve existing uncommitted changes in the same files).

## Completion

Implementation remains uncommitted. The configured implementation route ran as codex/gpt-6.1-terra medium; Claude Haiku was skipped because writing hooks were unverified. Focused tests were authored but not run by the implementer.

The sanctioned command `python -I "C:/Users/Micha Bigler/Documents/webapps/.claude/scripts/tests.py" --repo django-core-micha --scope backend` returned `RESULT: BLOCKED`: the repository has no project.yaml. No substitute test run was made.

Review round 2 dispatched envelope, regression, duplication, tests and security lenses on the implementation diff. All failed on the initial attempt and identical recovery: the Codex app-server client could not initialize due to access denied. No independent implementation review is claimed. Round 1 predates the implementation and does not review it.

BCO-CI-1 diagnosis remains blocked until the reviewed launcher is available through its supported installed runtime. No environment files were inspected. Keep this WO open; tests, review, release and BCO diagnosis remain outstanding.

The approved migration-diagnostic expansion was implemented alongside the existing uncommitted changes. With the repository's editable test virtual environment on PATH, the sanctioned `tests.py --repo django-core-micha --scope test-management-launcher` passed 42 tests, and `--scope test-management-isolation` passed 117 tests on the expanded diagnostic. The initial runs without that environment loaded an older installed package and failed; they are not evidence for this checkout. Local diagnostic starts identified the fixed system-check category `staticfiles` for both Cockpit and Musiknoten. The expansion is uncommitted and the prior independent review failed to launch, so neither review nor release is claimed. Worker canary coverage was unverified.

On 2026-11-22 the operator authorized independent replacement review outside the failed runner. The configured Claude Sonnet fallback was unavailable because its weekly quota was blocked until 2026-11-24 15:19 UTC; the independent OpenAI primary routes then ran on the exact selected diff. The first complete round found a false line-boundary classification and two test gaps; these were corrected without changing unrelated files. The sanctioned launcher and isolation scopes subsequently passed 46 and 117 tests respectively. The independent closing review used codex/gpt-6.1-terra and codex/gpt-6-luna for envelope, regression, duplication, tests and security. It raised one non-blocking defect: `ProgrammingError` may describe database programming failures other than schema errors, so the schema category can be misleading. The finding is accepted and carried. The review is closed; release and the separately owned BCO diagnosis remain open.

The operator approved landing the jointly reviewed DCM-DX-15 changes and a supplementary independent review of release metadata. The prepared patch version is 3.41.4. A focused codex/gpt-6.1-terra review of the version and changelog hunks reported no findings; its first attempt was rejected by the wrapper for its output format, and the formatted retry passed. The WO remains in review until release and BCO verification are complete.
