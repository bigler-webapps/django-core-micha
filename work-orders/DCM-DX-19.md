# DCM-DX-19 — Permanently dispose of one verified local test instance

## A. Envelope

Tier 3. Target repository: django-core-micha, main. BCO-ING-2, BCO-EXP-3 and BCO-UI-8 depend on the resulting lifetime guarantee.

### Goal and expected outcome

A local operator can explicitly end the lifetime of one identified, disposable test-management instance. On success the instance cannot resume and its synthetic identities, sessions and evidence are gone from the dedicated database and media storage. A second instance and the ordinary development stack retain their data. BCO needs this boundary for a technical account that outlives any individual run.

### Scope and compatibility

- Provide an explicit terminal operation in the local test-management launcher. Identify the instance through its owning checkout, consumer and opaque instance handle. Document its entry conditions, result and relationship to ordinary stop/resume.
- Limit disposal to verified, exclusively owned resources. Verify the local daemon, runtime identity, mounts, volumes and network before destructive effects. Refuse ambiguous or shared ownership and unsupported environments. Preserve host source, build artifacts, shared images, other instances and unrelated data.
- Existing start, stop, resume and run-level reset/close retain their contracts. A stopped instance remains recoverable until explicit disposal. The operation applies only to the selected instance.
- Repeated invocation, interrupted disposal or contention with another lifecycle action must yield an accurate outcome. A partial disposal must remain diagnosable. Recovery must stay within the original set of eligible resources.
- Keep diagnostics secret-free. BCO accounts, mailbox and expense cleanup, consumer pins, workspace runner integration, production infrastructure and scheduled cleanup are outside this shared-core WO.

### Existing capabilities and prerequisites

The shared inventory identifies the Django core launcher and controller. The launcher already verifies the consumer, handles, local runtime and mounts. Stop preserves dedicated database and media volumes for resume. The UI core has no role in Docker lifecycle operations. Record the launcher version actually exercised because the globally installed run-dev can differ from the checkout.

### Risks and required tests to write

1. Exercise the launcher with two identified synthetic instances holding distinguishable database and media markers. Dispose of one. Prove its containers and dedicated persistent data are gone, its handle cannot resume it, and the other instance still serves its original markers. Check host source and build inputs remain present.
2. Verify ordinary stop/resume preserves both markers. Cover existing start and run-level reset/close behavior affected by the change and any consumer flavours affected by shared validation.
3. Exercise a foreign checkout or handle, non-local daemon, unexpected mount and shared or inconsistent storage ownership. A refused preflight removes nothing. Foreign references cannot enlarge the target set.
4. Exercise interruption, retry and contention with a competing lifecycle action. Verify accurate final status, safe retry or explicit refusal, and preservation of unrelated resources.
5. Demonstrate actual removal of persistent synthetic data through the new operation using dedicated local fixtures. A mocked command invocation or a stopped container cannot establish the integration result.

## B. Implementation map

- `src/django_core_micha/scripts/run_dev.py` exposes the test-management launcher options.
- `src/django_core_micha/scripts/isolated_test_start.py` owns start, resume, stop and local runtime validation.
- `docs/design/test-management.md` describes handle ownership, named volumes, recovery and the stop contract.
- `tests/test_test_management_launcher.py`, `tests/test_test_management_consumers.py` and `tests/test_test_management_isolation.py` cover launcher and isolation boundaries.
- `tests/test_test_management_postgis_proof.py` and `tests/test_test_management_pgvector_proof.py` cover supported database variants.
- `project.yaml` defines the test-management-launcher and test-management-isolation scopes. The workspace acceptance runner currently invokes the recovery-preserving stop.

## Preamble

> **You are the dispatched implementer of this work order, not its Orchestrator. Rules in `AGENTS.md`
> and in skills that address the Orchestrator, a Codex parent or the Expertenchat (delegation, dispatch,
> review, register, landing) do not apply to you. You never run `codex` or `codex exec`, never write to
> git, and never edit the register or `.claude/codex-status.md`.**
>
> **Your first tool action is the hook canary: run the shell command `codex-hook-canary-ws-gov-60`
> exactly (it is a command string, not a file). The expected result is a denial,
> `WS-GOV-60 canary: workspace hooks are active`; continue after it. If it is not denied, report
> that once as unverified hook coverage and continue.**
>
> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> **Running your tests in the sandbox (WS-GOV-110, measured).** Django-core / Django app pytest: use the
> repo's own interpreter, `<repo>\.venv-test\Scripts\python.exe -m pytest -p no:cacheprovider <file>`; the bare
> `python` has no `django_core_micha`. Vitest works only when your writable root is that repo
> (`node node_modules/vitest/vitest.mjs run <file>`; with another root it fails with EPERM on
> `node_modules\.vite-temp`). Workspace gate suites: `python -I -B <test file>`. Never redirect a cache to
> make a tool work and never start a database or Docker. A test run that cannot work in the sandbox is NOT
> a blocker: when the code is complete and only the run is missing, end `RESULT: DONE` and add the line
> `TESTS NOT RUN: <reason>` (known reasons: needs PostgreSQL/Docker/WSL; SWC native addon refuses any
> sandbox cache folder, so Vitest on an SWC-plugin frontend such as jg-ferien cannot start). Use
> `RESULT: BLOCKED` only when the code itself cannot be completed.
>
> **Files.** Read a file over ~400 lines by locating with `Grep -n` and reading only the needed
> range — never the whole file, never the same range twice without a change in between. Write new code
> into cohesive modules named by behaviour: a new source or test file stays under ~400 lines; code
> added to a file already over ~800 lines goes into a new module unless it is a small change to
> existing logic. Name test files by the behaviour they cover, never by the work-order ID.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

## Verification and handover

The Orchestrator records scoped launcher and isolation evidence through tests.py, including new tests in a declared scope, and obtains the independent general and security reviews. Record positive disposal, refusal without mutation, stop/resume compatibility, retry and second-instance results. Apply the workspace approval rules to any live destructive Docker operation. Identify the consumer-installable version and the version used in the proof. BCO must still verify its own protection account, mailbox and expense lifecycles.

## Completion

The launcher implements the terminal operation, shared
per-instance lifecycle locking, preflight ownership checks and a durable
fail-closed interruption marker. The installed launcher is 3.41.4; the tested
checkout is 3.42.0. Publish run 45067571331 passed, and PyPI confirmed 3.42.0
on 2026-11-22. Part B: accurate;
implementation additionally requires focused lifecycle modules, a launcher
CLI test and opt-in proof configuration.

Checkout-scoped `tests.py` evidence: launcher 91 passed, isolation 161 passed.
The simulated tests cover refusal, running and stopped instances, interruption,
contention, and preservation of another instance. These do not prove real data
removal. On 2026-11-22 the operator approved the named two-instance local Docker
proof. `tests.py --scope test-management-disposal-proof` passed 1 test with no
skips. Beforehand, neither named instance had containers, volumes or a network.
The proof wrote distinguishable database and media markers, stopped and resumed
both instances, disposed of the first through the checkout launcher API and
verified both markers of the second. Afterwards the first instance's containers,
volumes and network were absent; the second instance's containers were stopped
and its volumes and network remained. Host source and build inputs survived. The
proof exercised the 3.42.0 checkout, not the globally installed 3.41.4 run-dev.
The ordinary test scopes were corrected to import the checkout.

Independent rounds 1 and 2 used codex/gpt-6.1-terra and codex/gpt-6-luna for
envelope, regression, duplication, tests and security. They identified missing
real proof, lock ownership, test isolation and coverage defects. Corrections
after the closing review touch the previously reviewed lock, proof, tests and
scope files; they received no further review. Round 3 independently reviewed
only the newly included version and pytest-option files. Its envelope finding
that disposal was missing is inapplicable to this explicitly limited payload.
There are no accepted open review findings. BCO remains responsible for its own
account, mailbox and expense lifecycles.

## Gate record

Gate #1 approved by the operator 2026-11-22 (explicit approval to record Gate #1 and start a dedicated Orchestrator chat for DCM-DX-19).
